<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

// メニューを出すかだけの判定。権限の実体は admin-users の API (ADR D9)
const isAdmin = computed(() => auth.groups.includes('admin'))
</script>

<template>
  <div>
    <h1 class="text-h5 mb-1">
      入り方の選択
    </h1>
    <p class="text-body-2 text-medium-emphasis mb-6">
      使い方を選んでください。
    </p>

    <v-row>
      <v-col
        cols="12"
        md="6"
      >
        <v-card
          data-test="start-device"
          :to="{ name: 'devices' }"
          height="100%"
          border
          flat
        >
          <v-card-item>
            <template #prepend>
              <v-icon
                icon="mdi-router-wireless"
                color="primary"
                size="40"
              />
            </template>
            <v-card-title>通信機単位</v-card-title>
            <v-card-subtitle>通信機ログインからサービスを使う</v-card-subtitle>
          </v-card-item>
          <v-card-text class="text-body-2">
            割り当てられた通信機を選び、暗証番号で通信機ログインをします。その通信機で許可されたサービス (カメラ画像判定など) の一覧に進みます。
          </v-card-text>
        </v-card>
      </v-col>
      <v-col
        cols="12"
        md="6"
      >
        <v-card
          data-test="start-account"
          :to="{ name: 'account' }"
          height="100%"
          border
          flat
        >
          <v-card-item>
            <template #prepend>
              <v-icon
                icon="mdi-account-circle-outline"
                color="primary"
                size="40"
              />
            </template>
            <v-card-title>アカウント単位</v-card-title>
            <v-card-subtitle>自分のアカウントの情報を見る</v-card-subtitle>
          </v-card-item>
          <v-card-text class="text-body-2">
            パスキーの管理と、割り当てられた通信機と許可されたサービスの一覧を見ます。
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-card
      v-if="isAdmin"
      variant="tonal"
      class="mt-6"
    >
      <v-card-text class="d-flex align-center flex-wrap ga-3">
        <span class="text-body-2">管理者の方は、利用者の招待・通信機の利用期間・割り当て・サービスの許可をここから行います。</span>
        <v-spacer />
        <v-btn
          data-test="start-admin"
          :to="{ name: 'admin-users.users' }"
          color="primary"
          variant="flat"
          prepend-icon="mdi-account-cog-outline"
        >
          利用者の管理
        </v-btn>
      </v-card-text>
    </v-card>
  </div>
</template>
