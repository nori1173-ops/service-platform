<script setup lang="ts">
import { onUnmounted, ref } from 'vue'

// 一時パスワードを 1 回だけ見せる。値は親のメモリだけが持ち、閉じたら親が捨てる (ストアに残さない)
const open = defineModel<boolean>({ required: true })

const props = defineProps<{ secret: string; email: string | null }>()

const copied = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | null = null

async function copy(): Promise<void> {
  if (!navigator.clipboard) {
    return
  }
  try {
    await navigator.clipboard.writeText(props.secret)
  } catch {
    return
  }
  copied.value = true
  if (copiedTimer !== null) {
    clearTimeout(copiedTimer)
  }
  copiedTimer = setTimeout(() => {
    copied.value = false
  }, 2000)
}

onUnmounted(() => {
  if (copiedTimer !== null) {
    clearTimeout(copiedTimer)
  }
})
</script>

<template>
  <v-dialog
    v-model="open"
    max-width="520"
    persistent
  >
    <v-card data-test="one-time-dialog">
      <v-card-item>
        <v-card-title>一時パスワード</v-card-title>
        <v-card-subtitle>{{ email }}</v-card-subtitle>
      </v-card-item>
      <v-card-text>
        <v-alert
          type="warning"
          variant="tonal"
          density="compact"
          class="mb-4"
        >
          この一時パスワードは今だけ表示します。閉じると二度と表示できません。社内の別経路 (対面・電話など) で本人に渡してください。メールやチャットに貼らないでください。有効期限は 3 日です。
        </v-alert>
        <div class="d-flex align-center justify-center ga-2">
          <code
            data-test="one-time-secret"
            class="text-h6"
          >{{ secret }}</code>
          <v-btn
            data-test="one-time-copy"
            :icon="copied ? 'mdi-check' : 'mdi-content-copy'"
            :title="copied ? 'コピーしました' : 'コピー'"
            variant="text"
            size="small"
            @click="copy"
          />
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="one-time-close"
          color="primary"
          variant="flat"
          @click="open = false"
        >
          控えたので閉じる
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
