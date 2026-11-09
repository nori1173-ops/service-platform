import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 着地の近道  で通信機ログインの画面へ来たときの通信機アカウント。
 * ガードが付け、通信機ログインの画面を離れるか、条件が崩れるか、サインアウトで消す
 */
export const useLandingStore = defineStore('landing', () => {
  const account = ref<string | null>(null)

  function enter(value: string): void {
    account.value = value
  }

  function clear(): void {
    account.value = null
  }

  return { account, enter, clear }
})
