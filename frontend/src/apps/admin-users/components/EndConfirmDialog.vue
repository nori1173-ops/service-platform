<script setup lang="ts">
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const open = defineModel<boolean>({ required: true })

const props = withDefaults(defineProps<{ account: string; orgName: string; loading?: boolean }>(), {
  loading: false,
})

const emit = defineEmits<{ confirm: [] }>()

// 通信機を次のお客様へ渡す手順 (ADR D4)。2 と 3 はこの画面の外の作業
const HANDOVER_STEPS = [
  'この通信機の利用期間を終える (この画面)',
  'カメラの利用期間を終了する (画像と設定を消す)',
  '通信機の暗証番号を変える (通信機の設定を扱う担当の手順。新しい暗証番号は、通信機が新しい設定を報告した後に効く)',
  '新しい利用期間を「割り当てだけ」で始め、次のお客様の利用者を割り当て、サービスの許可を出し、カメラの利用期間を始める',
  '新しい暗証番号が効いた (通信機が新しい設定を報告した) ことを確かめてから、必要なら「誰でも」に切り替える',
]

function confirm(): void {
  if (!props.loading) {
    emit('confirm')
  }
}
</script>

<template>
  <ConfirmDialog
    v-model="open"
    title="通信機の利用期間を終えますか?"
    :message="`${account} / ${orgName} の利用期間を終えます。開いている割り当てと許可を同じ時刻で終え、割り当てられた利用者の通信機ログインも切れます。行は消さずに履歴として残ります。`"
    warning="この操作は取り消せません。同じ通信機を続けて使うときも、利用期間を始め直して割り当てと許可を出し直します。"
    confirm-label="利用期間を終える"
    confirm-color="warning"
    :loading="loading"
    @confirm="confirm"
  >
    <p class="text-body-2 font-weight-medium mt-4 mb-1">
      次のお客様へ渡すときの引き渡しの手順
    </p>
    <ol
      data-test="handover-steps"
      class="text-body-2 ps-5"
    >
      <li
        v-for="step in HANDOVER_STEPS"
        :key="step"
      >
        {{ step }}
      </li>
    </ol>
    <p class="text-caption text-medium-emphasis mt-2 mb-0">
      2 と 3 はこの画面の外の作業です。終えた後に忘れずに行ってください。
    </p>
  </ConfirmDialog>
</template>
