package com.eodigaji.app // ← 앱 프로젝트의 패키지명으로 바꾸세요

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

/**
 * 비밀 도감 (visited_spots).
 *
 * - 사용자가 [도감에 저장]을 눌렀을 때만 [save] 합니다. 자동 저장은 하지 않습니다.
 * - 자동 삭제 규칙은 없습니다. 사용자가 [delete](개별) 또는 [deleteAll](전체)로 지웁니다.
 * - 외부 라이브러리 없이 Android 기본 SQLiteOpenHelper 만 씁니다.
 * - DB 작업은 메인 스레드에서 하지 마세요: `withContext(Dispatchers.IO) { db.save(...) }`
 * - 좌표는 저장하지 않습니다(개인정보 최소 수집).
 */
data class VisitedSpot(
    val id: Long = 0,                  // 저장 후 DB 가 부여
    val contentId: String,             // reveal.content_id
    val blindName: String,             // 그날 앱이 고른 가명
    val realName: String,
    val address: String?,
    val imageUrl: String?,
    val theme: String?,
    val keywordIds: List<String>,      // reveal.matched_keywords
    val feedback: Int?,                // 1=좋아요, -1=별로, null=미응답
    val visitedAt: Long,               // epoch millis
) {
    companion object {
        /** 추천 응답의 reveal 과 앱이 고른 가명으로 도감 항목을 만듭니다. */
        fun from(
            reveal: Reveal,
            blindName: String,
            feedback: Int? = null,
            visitedAt: Long = System.currentTimeMillis(),
        ) = VisitedSpot(
            contentId = reveal.contentId,
            blindName = blindName,
            realName = reveal.realName,
            address = reveal.address.ifBlank { null },
            imageUrl = reveal.imageUrl,
            theme = reveal.theme,
            keywordIds = reveal.matchedKeywords,
            feedback = feedback,
            visitedAt = visitedAt,
        )
    }
}

class VisitedSpotsDb(context: Context) :
    SQLiteOpenHelper(context.applicationContext, DB_NAME, null, DB_VERSION) {

    companion object {
        private const val DB_NAME = "eodigaji.db"
        private const val DB_VERSION = 1
        private const val T = "visited_spots"

        const val FEEDBACK_LIKE = 1
        const val FEEDBACK_DISLIKE = -1

        @Volatile private var instance: VisitedSpotsDb? = null

        /** 앱 전체에서 하나만 씁니다. */
        fun get(context: Context): VisitedSpotsDb =
            instance ?: synchronized(this) {
                instance ?: VisitedSpotsDb(context).also { instance = it }
            }
    }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS $T (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id   TEXT    NOT NULL,
                blind_name   TEXT    NOT NULL,
                real_name    TEXT    NOT NULL,
                address      TEXT,
                image_url    TEXT,
                theme        TEXT,
                keyword_ids  TEXT,
                feedback     INTEGER,
                visited_at   INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_visited_at ON $T(visited_at DESC)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // 버전 1 뿐입니다. 컬럼을 바꾸면 DB_VERSION 을 올리고 여기서 ALTER TABLE 로 옮기세요(데이터 삭제 금지).
    }

    /** [도감에 저장] 버튼. 새 행의 id 를 돌려줍니다(실패 시 -1). 같은 곳을 다시 가면 새 기록으로 쌓입니다. */
    fun save(spot: VisitedSpot): Long =
        writableDatabase.insert(T, null, spot.toValues())

    /** 도감 목록, 최근 방문 순. */
    fun all(): List<VisitedSpot> =
        readableDatabase.query(T, null, null, null, null, null, "visited_at DESC").use { c ->
            buildList { while (c.moveToNext()) add(c.toSpot()) }
        }

    fun byId(id: Long): VisitedSpot? =
        readableDatabase.query(T, null, "id = ?", arrayOf(id.toString()), null, null, null).use { c ->
            if (c.moveToFirst()) c.toSpot() else null
        }

    /** 이 장소를 도감에 저장한 적이 있는지 (같은 곳 재방문 표시 등). */
    fun hasVisited(contentId: String): Boolean =
        readableDatabase.rawQuery("SELECT 1 FROM $T WHERE content_id = ? LIMIT 1", arrayOf(contentId))
            .use { it.moveToFirst() }

    fun count(): Int =
        readableDatabase.rawQuery("SELECT COUNT(*) FROM $T", null).use { c ->
            if (c.moveToFirst()) c.getInt(0) else 0
        }

    /** 좋아요(1) / 별로(-1) / 취소(null). 바뀐 행 수를 돌려줍니다. */
    fun updateFeedback(id: Long, feedback: Int?): Int {
        require(feedback == null || feedback == FEEDBACK_LIKE || feedback == FEEDBACK_DISLIKE) {
            "feedback 은 1, -1, null 중 하나여야 합니다"
        }
        val v = ContentValues().apply {
            if (feedback == null) putNull("feedback") else put("feedback", feedback)
        }
        return writableDatabase.update(T, v, "id = ?", arrayOf(id.toString()))
    }

    /** 개별 삭제. 지운 행 수를 돌려줍니다. */
    fun delete(id: Long): Int =
        writableDatabase.delete(T, "id = ?", arrayOf(id.toString()))

    /** 전체 삭제. 지운 행 수를 돌려줍니다. */
    fun deleteAll(): Int =
        writableDatabase.delete(T, "1", null)

    private fun VisitedSpot.toValues() = ContentValues().apply {
        put("content_id", contentId)
        put("blind_name", blindName)
        put("real_name", realName)
        put("address", address)
        put("image_url", imageUrl)
        put("theme", theme)
        put("keyword_ids", keywordIds.joinToString(","))
        if (feedback == null) putNull("feedback") else put("feedback", feedback)
        put("visited_at", visitedAt)
    }

    private fun Cursor.toSpot(): VisitedSpot {
        fun str(name: String): String? =
            getColumnIndexOrThrow(name).let { if (isNull(it)) null else getString(it) }
        val fbIdx = getColumnIndexOrThrow("feedback")
        return VisitedSpot(
            id = getLong(getColumnIndexOrThrow("id")),
            contentId = str("content_id")!!,
            blindName = str("blind_name")!!,
            realName = str("real_name")!!,
            address = str("address"),
            imageUrl = str("image_url"),
            theme = str("theme"),
            keywordIds = str("keyword_ids").orEmpty().split(',').filter { it.isNotBlank() },
            feedback = if (isNull(fbIdx)) null else getInt(fbIdx),
            visitedAt = getLong(getColumnIndexOrThrow("visited_at")),
        )
    }
}
