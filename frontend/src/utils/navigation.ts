/** 別のビルドの画面 (カメラの SPA の /camera/ など) へ同じタブでページごと移る (ADR D6) */
export function leaveTo(path: string): void {
  window.location.assign(path)
}

/** 履歴に残さずに移る (着地の近道で、戻るで / に戻ってまた移る、を避ける) */
export function replaceTo(path: string): void {
  window.location.replace(path)
}
