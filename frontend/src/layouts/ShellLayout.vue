<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { apps } from '@/apps/registry'
import { SITE_ENV_COLORS, SITE_ENV_LABELS, siteEnv } from '@/config/site'
import { useAuthStore } from '@/stores/auth'
import { useMeStore } from '@/stores/me'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const me = useMeStore()
const ui = useUiStore()
const { mdAndUp } = useDisplay()

// 狭い画面ではドロワーが一時表示で画面を覆うため、閉じた状態から始める（UI-02）
watch(mdAndUp, (wide) => { ui.drawer = wide }, { immediate: true })

function closeOnNarrow(): void {
  if (!mdAndUp.value) {
    ui.drawer = false
  }
}

function allowed(requiredGroups?: string[]): boolean {
  return !requiredGroups?.length || requiredGroups.some((group) => auth.groups.includes(group))
}

// 停止中・admin の準備中 / 確認待ちはアカウントの画面だけを使う (ガードと同じ判定)
const visibleApps = computed(() =>
  me.restricted ? [] : apps.filter((app) => allowed(app.requiredGroups)),
)

// dev サイトと本番サイトの取り違えを防ぐため常時表示する（UI-12）
const site = computed(() => siteEnv())
const siteLabel = computed(() => SITE_ENV_LABELS[site.value])
const siteColor = computed(() => SITE_ENV_COLORS[site.value])

const currentApp = computed(() => apps.find((app) => app.id === route.meta.appId))

const currentNav = computed(() =>
  (currentApp.value?.nav ?? []).filter((item) =>
    allowed(item.requiredGroups ?? currentApp.value?.requiredGroups),
  ),
)

function toggleNav(): void {
  if (mdAndUp.value) {
    ui.toggleRail()
  } else {
    ui.toggleDrawer()
  }
}

async function logout(): Promise<void> {
  try {
    await auth.logout()
  } catch {
    ui.notify('サインアウトに失敗しましたが、この端末からはサインアウトしました', 'warning')
  }
  await router.push({ name: 'login' })
}
</script>

<template>
  <v-app>
    <v-navigation-drawer
      v-model="ui.drawer"
      :rail="ui.rail && mdAndUp"
      :temporary="!mdAndUp"
      :permanent="mdAndUp"
      expand-on-hover
      width="260"
    >
      <v-list
        density="compact"
        nav
      >
        <template v-if="!me.restricted">
          <v-list-item
            prepend-icon="mdi-home-outline"
            title="ホーム"
            :to="{ name: 'home' }"
            exact
            @click="closeOnNarrow"
          />
          <v-divider class="my-1" />
        </template>
        <v-list-item
          v-for="app in visibleApps"
          :key="app.id"
          :prepend-icon="app.icon"
          :title="app.title"
          :to="`/${app.id}`"
          @click="closeOnNarrow"
        />
      </v-list>

      <template #append>
        <v-list
          density="compact"
          nav
        >
          <v-list-item
            prepend-icon="mdi-account-circle-outline"
            title="アカウント"
            :to="{ name: 'account' }"
            @click="closeOnNarrow"
          />
          <v-list-item
            prepend-icon="mdi-logout"
            title="サインアウト"
            @click="logout"
          />
        </v-list>
      </template>
    </v-navigation-drawer>

    <v-app-bar
      density="comfortable"
      flat
      border="b"
    >
      <v-app-bar-nav-icon @click="toggleNav" />
      <v-chip
        data-test="site-env"
        :color="siteColor"
        variant="flat"
        size="small"
        label
        class="me-3 flex-0-0"
      >
        {{ siteLabel }}
      </v-chip>
      <v-app-bar-title>{{ currentApp?.title ?? 'SERVICE-PLATFORM' }}</v-app-bar-title>
      <template #append>
        <!-- トグル自身の説明はヘルプモードに関わらず常時出す -->
        <v-tooltip
          v-if="currentApp?.helpMode"
          text="ON にすると各ボタン・入力欄の説明がマウスオーバーで表示されます"
          location="bottom"
          max-width="320"
          content-class="help-tip"
        >
          <template #activator="{ props }">
            <v-btn
              v-bind="props"
              data-test="help-toggle"
              icon="mdi-help-circle-outline"
              :color="ui.helpMode ? 'primary' : undefined"
              :variant="ui.helpMode ? 'tonal' : 'text'"
              aria-label="操作説明の表示切替"
              :aria-pressed="ui.helpMode ? 'true' : 'false'"
              class="me-2"
              @click="ui.toggleHelpMode"
            />
          </template>
        </v-tooltip>
        <v-btn
          data-test="account-link"
          :to="{ name: 'account' }"
          variant="text"
          prepend-icon="mdi-account-circle-outline"
          class="text-none"
          aria-label="アカウント"
        >
          <span class="d-none d-sm-inline">{{ auth.email }}</span>
        </v-btn>
        <v-btn
          data-test="appbar-signout"
          icon="mdi-logout"
          variant="text"
          title="サインアウト"
          aria-label="サインアウト"
          @click="logout"
        />
      </template>
      <template
        v-if="currentNav.length"
        #extension
      >
        <v-tabs
          :model-value="route.path"
          density="compact"
        >
          <v-tab
            v-for="item in currentNav"
            :key="item.to"
            :value="item.to"
            :to="item.to"
            :prepend-icon="item.icon"
          >
            {{ item.title }}
          </v-tab>
        </v-tabs>
      </template>
    </v-app-bar>

    <v-main>
      <v-container
        fluid
        class="pa-4 pa-md-6"
      >
        <router-view v-slot="{ Component }">
          <keep-alive :max="5">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </v-container>
    </v-main>

    <v-snackbar
      v-model="ui.snackbar.visible"
      :color="ui.snackbar.color"
      :timeout="ui.snackbar.color === 'error' ? -1 : 5000"
      location="bottom right"
    >
      {{ ui.snackbar.text }}
      <template #actions>
        <v-btn
          data-test="snackbar-close"
          variant="text"
          @click="ui.snackbar.visible = false"
        >
          閉じる
        </v-btn>
      </template>
    </v-snackbar>
  </v-app>
</template>

<style>
/* オーバーレイは body へ teleport されるため scoped にしない（HelpTip.vue と同じ体裁） */
.help-tip {
  line-height: 1.5;
  white-space: normal;
}
</style>
