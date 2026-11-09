<script setup lang="ts">
import { computed, onActivated, ref, watch } from 'vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useUiStore } from '@/stores/ui'
import { DASH } from '@/utils/format'
import AddMemberDialog from '../components/AddMemberDialog.vue'
import { formatJst } from '../labels'
import { pathLabel } from '../periodLabels'
import { useGroupsStore } from '../stores/groups'
import { useUsersStore } from '../stores/users'
import type { GroupMember } from '../types'

const props = defineProps<{ id: string }>()

const STATE_LABELS = { current: '有効', upcoming: 'これから', ended: '終了' } as const

const store = useGroupsStore()
const usersStore = useUsersStore()
const ui = useUiStore()
const dialog = ref<'member' | 'remove' | null>(null)
const removing = ref<GroupMember | null>(null)
const dialogError = ref<string | null>(null)

const group = computed(() => (store.detail?.group_id === props.id ? store.detail : null))
const active = computed(() => group.value?.status === 'active')
const busy = computed(() => store.detailLoading || store.working)
const label = computed(() => pathLabel(group.value?.path_names))

function close(): void {
  dialog.value = null
  removing.value = null
  dialogError.value = null
}

function load(): void {
  void store.fetchDetail(props.id)
  void usersStore.fetch()
}

async function addMember(userId: string): Promise<void> {
  const current = group.value
  if (!current) return
  const result = await store.addMember(current.group_id, userId)
  if (result.ok) {
    close()
    ui.notify('グループに属させました', 'success')
  } else {
    dialogError.value = result.message
  }
}

function askRemove(member: GroupMember): void {
  if (busy.value) return
  removing.value = member
  dialog.value = 'remove'
}

async function remove(): Promise<void> {
  const current = group.value
  const member = removing.value
  if (!current || !member) return
  const result = await store.removeMember(current.group_id, member.user_id)
  close()
  ui.notify(result.ok ? 'グループから外しました' : result.message, result.ok ? 'success' : 'error')
}

onActivated(load)
watch(
  () => props.id,
  () => {
    close()
    load()
  },
  { immediate: true },
)
</script>

<template>
  <div>
    <v-alert
      v-if="store.detailError"
      data-test="group-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ store.detailError }}
    </v-alert>
    <v-progress-linear
      v-if="store.detailLoading"
      indeterminate
      color="primary"
      class="mb-4"
    />
    <template v-if="group">
      <div class="d-flex align-center flex-wrap ga-2 mb-2">
        <h1
          class="text-h5 mb-0"
          data-test="group-path"
        >
          {{ label || group.name }}
        </h1>
        <v-chip
          size="small"
          :color="active ? 'success' : 'grey'"
        >
          {{ active ? '稼働中' : '終了' }}
        </v-chip>
      </div>
      <p class="text-body-2 text-medium-emphasis mb-1">
        {{ group.depth }} 段目・作成 {{ formatJst(group.created_at) }}
      </p>
      <p
        v-if="group.note"
        class="text-body-2 text-pre-wrap mb-3"
      >
        {{ group.note }}
      </p>

      <v-card
        variant="flat"
        border
        class="mb-4"
      >
        <v-card-item>
          <v-card-title>子のグループ</v-card-title>
        </v-card-item>
        <v-card-text>
          <div
            v-if="!group.children.length"
            class="text-body-2 text-medium-emphasis"
          >
            子のグループはありません。
          </div>
          <ul v-else>
            <li
              v-for="child in group.children"
              :key="child.group_id"
              :class="{ 'text-disabled': child.status === 'ended' }"
            >
              <RouterLink :to="{ name: 'admin-users.group', params: { id: child.group_id } }">
                {{ child.name }}
              </RouterLink>
            </li>
          </ul>
        </v-card-text>
      </v-card>

      <v-card
        variant="flat"
        border
        class="mb-4"
      >
        <v-card-item>
          <v-card-title>所属する人</v-card-title>
          <template #append>
            <v-btn
              v-if="active"
              data-test="member-open"
              variant="tonal"
              color="primary"
              prepend-icon="mdi-account-plus-outline"
              :disabled="busy || usersStore.loading"
              @click="dialog = 'member'"
            >
              属させる
            </v-btn>
          </template>
        </v-card-item>
        <v-card-text>
          <div
            v-if="!group.members.length"
            class="text-body-2 text-medium-emphasis"
          >
            属する人はいません。
          </div>
          <v-table
            v-else
            density="compact"
          >
            <thead>
              <tr>
                <th>利用者</th>
                <th>組織</th>
                <th>属した日時</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="member in group.members"
                :key="member.membership_id"
                :data-test="`member-${member.user_id}`"
              >
                <td>
                  <RouterLink :to="{ name: 'admin-users.user', params: { userId: member.user_id } }">
                    {{ member.email ?? member.user_id }}
                  </RouterLink>
                </td>
                <td>{{ member.org_name ?? DASH }}</td>
                <td class="text-no-wrap">
                  {{ formatJst(member.added_at) }}
                </td>
                <td class="text-end">
                  <v-btn
                    :data-test="`member-remove-${member.user_id}`"
                    variant="text"
                    color="warning"
                    size="small"
                    :disabled="busy"
                    @click="askRemove(member)"
                  >
                    外す
                  </v-btn>
                </td>
              </tr>
            </tbody>
          </v-table>
        </v-card-text>
      </v-card>

      <v-card
        variant="flat"
        border
      >
        <v-card-item>
          <v-card-title>このグループ宛の割り当て</v-card-title>
        </v-card-item>
        <v-card-text>
          <div
            v-if="!group.assignments.length"
            class="text-body-2 text-medium-emphasis"
          >
            割り当てはありません。割り当ては通信機の利用期間の画面で作ります。
          </div>
          <v-table
            v-else
            density="compact"
          >
            <thead>
              <tr>
                <th>通信機アカウント</th>
                <th>組織</th>
                <th>有効期間</th>
                <th>状態</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in group.assignments"
                :key="item.assignment_id"
                :class="{ 'text-disabled': item.state === 'ended' }"
              >
                <td>
                  <RouterLink :to="{ name: 'admin-users.device-period', params: { id: item.device_period_id } }">
                    {{ item.account }}
                  </RouterLink>
                </td>
                <td>{{ item.org_name ?? DASH }}</td>
                <td class="text-no-wrap">
                  {{ formatJst(item.valid_from) }} 〜 {{ item.valid_to ? formatJst(item.valid_to) : '利用期間の終わりまで' }}
                </td>
                <td>{{ STATE_LABELS[item.state] }}</td>
              </tr>
            </tbody>
          </v-table>
        </v-card-text>
      </v-card>

      <AddMemberDialog
        :model-value="dialog === 'member'"
        :users="usersStore.users"
        :member-ids="group.members.map((member) => member.user_id)"
        :group-label="label"
        :loading="busy"
        :error="dialogError"
        @update:model-value="(value: boolean) => !value && close()"
        @submit="addMember"
      />
      <ConfirmDialog
        :model-value="dialog === 'remove'"
        title="グループから外しますか?"
        :message="removing ? `${removing.email ?? removing.user_id} を ${label} から外します。` : ''"
        warning="このグループ宛の割り当てで使っていた通信機には、ほかの根拠が無ければ次の操作から入れなくなります。"
        confirm-label="外す"
        :loading="busy"
        @update:model-value="(value: boolean) => !value && close()"
        @confirm="remove"
      />
    </template>
  </div>
</template>
