package com.eodigaji.app // ← 앱 프로젝트의 패키지명으로 바꾸세요

import android.content.Context
import android.content.SharedPreferences

/**
 * 최근 사용한 가명 기록 (같은 가명이 연달아 나오지 않게).
 *
 * - 최근 [MAX]개를 SharedPreferences 에 줄바꿈으로 이어 저장합니다. 최신이 맨 앞입니다.
 * - 가명을 고를 때: `BlindNames.pick(RecentBlindNames.load(prefs))`
 * - 안내를 시작할 때: `RecentBlindNames.push(prefs, blindName)`
 * - prefs 는 `RecentBlindNames.prefs(context)` 로 얻으면 모두 같은 파일을 씁니다.
 */
object RecentBlindNames {
    private const val PREFS_NAME = "eodigaji_prefs"
    private const val KEY = "recent_blind_names"
    const val MAX = 10

    fun prefs(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** 최근 사용 가명 목록 (최신이 맨 앞). 없으면 빈 목록. */
    fun load(prefs: SharedPreferences): List<String> =
        prefs.getString(KEY, null).orEmpty()
            .split('\n')
            .filter { it.isNotBlank() }

    /** 방금 쓴 가명을 맨 앞에 넣습니다. 이미 있으면 앞으로 옮기고, [MAX]개를 넘으면 오래된 것부터 버립니다. */
    fun push(prefs: SharedPreferences, name: String) {
        if (name.isBlank()) return
        val updated = (listOf(name) + load(prefs).filterNot { it == name }).take(MAX)
        prefs.edit().putString(KEY, updated.joinToString("\n")).apply()
    }

    /** 기록 전체 삭제 (설정 화면의 "기록 초기화" 등). */
    fun clear(prefs: SharedPreferences) {
        prefs.edit().remove(KEY).apply()
    }
}
