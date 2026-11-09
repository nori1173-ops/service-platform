<script setup lang="ts">
import { computed, onActivated, onMounted, ref } from 'vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useUiStore } from '@/stores/ui'
import GroupDialog from '../components/GroupDialog.vue'
import { formatJst } from '../labels'
import { useGroupsStore } from '../stores/groups'
import type { Group, GroupInput } from '../types'

const MAX_DEPTH = 3

const store = useGroupsStore()
const ui = useUiStore()
const dialog = ref<'create' | 'rename' | 'end' | null>(null)
const parent = ref<Group | null>(null)
const target = ref<Group | null>(null)
const dialogError = ref<string | null>(null)

const names = computed(() => new Map(store.groups.map((group) => [group.group_id, group.name])))
const busy = computed(() => store.loading || store.working)

function pathOf(group: Group): string {
  return group.path.map((id) => names.value.get(id) ?? '?').join(' > ')
}

function close(): void {
  dialog.value = null
  parent.value = null
  target.value = null
  dialogError.value = null
}

function openCreate(group: Group | null): void {
  if (busy.value) return
  parent.value = group ? { ...group, path_names: group.path.map((id) => names.value.get(id) ?? null) } : null
  dialogError.value = null
  dialog.value = 'create'
}

function openRename(group: Group): void {
  if (busy.value) return
  target.value = group
  dialogError.value = null
  dialog.value = 'rename'
}

function openEnd(group: Group): void {
  if (busy.value) return
  target.value = group
  dialogError.value = null
  dialog.value = 'end'
}

async function submit(input: GroupInput): Promise<void> {
  const result =
    dialog.value === 'rename' && target.value
      ? await store.update(target.value.group_id, { name: input.name, note: input.note })
      : await store.create(input)
  if (result.ok) {
    close()
    ui.notify('グループを保存しました', 'success')
  } else {
    dialogError.value = result.message
  }
}

async function end(): Promise<void> {
  if (!target.value) return
  const result = await store.end(target.value.group_id)
  close()
  ui.notify(result.ok ? 'グループを終えました' : result.message, result.ok ? 'success' : 'error')
}

let mounted = false
onMounted(() => {
  mounted = true
  void store.fetch()
})
onActivated(() => {
  if (mounted) {
    mounted = false
    return
  }
  void store.fetch()
})
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-2 mb-2">
      <h1 class="text-h5 mb-0">
        グループ
      </h1>
      <v-spacer />
      <v-btn-toggle
        :model-value="store.status"
        mandatory
        density="compact"
        variant="outlined"
        @update:model-value="store.fetch($event)"
      >
        <v-btn
          value="active"
          data-test="groups-filter-active"
        >
          稼働中
        </v-btn>
        <v-btn
          value="all"
          data-test="groups-filter-all"
        >
          すべて
        </v-btn>
      </v-btn-toggle>
      <v-btn
        data-test="group-create-root"
        color="primary"
        variant="flat"
        prepend-icon="mdi-plus"
        :disabled="busy"
        @click="openCreate(null)"
      >
        根のグループを作る
      </v-btn>
    </div>
    <p class="text-body-2 text-medium-emphasis mb-4">
      人の集まりの木です (3 段まで)。グループに通信機を割り当てると、そのグループと下の段のグループに属する人に効きます。
    </p>
    <v-alert
      v-if="store.error"
      data-test="groups-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ store.error }}
    </v-alert>
    <v-progress-linear
      v-if="store.loading"
      indeterminate
      color="primary"
    />
    <v-card
      variant="flat"
      border
    >
      <v-card-text>
        <div
          v-if="!store.loading && !store.groups.length"
          class="text-body-2 text-medium-emphasis"
        >
          グループはありません。
        </div>
        <v-table
          v-else
          density="compact"
        >
          <thead>
            <tr>
              <th>名前</th>
              <th class="text-no-wrap">
                作成
              </th>
              <th />
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="group in store.groups"
              :key="group.group_id"
              :data-test="`group-row-${group.group_id}`"
              :data-depth="group.depth"
              :class="{ 'text-disabled': group.status === 'ended' }"
            >
              <td :style="{ paddingLeft: `${16 + (group.depth - 1) * 24}px` }">
                <RouterLink
                  :data-test="`group-link-${group.group_id}`"
                  :to="{ name: 'admin-users.group', params: { id: group.group_id } }"
                  :title="pathOf(group)"
                >
                  {{ group.name }}
                </RouterLink>
                <v-chip
                  v-if="group.status === 'ended'"
                  size="x-small"
                  class="ms-2"
                >
                  終了
                </v-chip>
              </td>
              <td class="text-no-wrap">
                {{ formatJst(group.created_at) }}
              </td>
              <td class="text-end text-no-wrap">
                <template v-if="group.status === 'active'">
                  <v-btn
                    v-if="group.depth < MAX_DEPTH"
                    :data-test="`group-child-${group.group_id}`"
                    variant="text"
                    size="small"
                    :disabled="busy"
                    @click="openCreate(group)"
                  >
                    子を作る
                  </v-btn>
                  <v-btn
                    :data-test="`group-rename-${group.group_id}`"
                    variant="text"
                    size="small"
                    :disabled="busy"
                    @click="openRename(group)"
                  >
                    名前を変える
                  </v-btn>
                  <v-btn
                    :data-test="`group-end-${group.group_id}`"
                    variant="text"
                    size="small"
                    color="warning"
                    :disabled="busy"
                    @click="openEnd(group)"
                  >
                    終える
                  </v-btn>
                </template>
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-card-text>
    </v-card>
    <p class="text-caption text-medium-emphasis mt-2">
      終えられるのは、稼働中の子のグループ・所属・終わっていない割り当てが無いときだけです。親は後から変えられません (組み替えは新しいグループを作って移します)。
    </p>

    <GroupDialog
      :model-value="dialog === 'create' || dialog === 'rename'"
      :mode="dialog === 'rename' ? 'rename' : 'create'"
      :parent="parent"
      :initial-name="target?.name ?? ''"
      :initial-note="target?.note ?? null"
      :loading="store.working"
      :error="dialogError"
      @update:model-value="(value: boolean) => !value && close()"
      @submit="submit"
    />
    <ConfirmDialog
      :model-value="dialog === 'end'"
      title="グループを終えますか?"
      :message="target ? `${pathOf(target)} を終えます。行は消さずに履歴として残ります。` : ''"
      confirm-label="終える"
      :loading="store.working"
      @update:model-value="(value: boolean) => !value && close()"
      @confirm="end"
    />
  </div>
</template>
