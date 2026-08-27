/**
 * @file holiday_fetcher.cc
 * @brief Chinese official holiday schedule fetcher
 *
 * Fetches State Council holiday data from timor.tech API.
 * Response format:
 *   {"code":0, "holiday":{"2026-01-01":{"name":"元旦","rest":1},...}}
 *
 * - rest=1: holiday/rest day (休)
 * - rest=0: compensatory workday (补班)
 *
 * Data is cached in NVS under key "holiday_YYYY" (blob, ~2KB per year).
 */

#include "holiday_fetcher.h"
#include <esp_log.h>
#include <esp_http_client.h>
#include <esp_crt_bundle.h>
#include <nvs_flash.h>
#include <nvs.h>
#include <cstring>
#include <cstdio>
#include <ctime>
#include <algorithm>
#include <mutex>

namespace holiday_fetcher {

static constexpr char kTag[] = "HolidayFetcher";
static constexpr char kNvsNamespace[] = "holiday_cache";
static constexpr int kMaxResponseSize = 8192;

// Retain the current and following year so the calendar remains correct across
// New Year without replacing the cache that is still visible to the UI.
static constexpr int kCacheSlots = 2;
static HolidayCache s_caches[kCacheSlots] = {};
static bool s_loaded[kCacheSlots] = {};
// Protects s_caches/s_loaded: writers are Init()/Fetch() (online-data task),
// readers are the UI task.
static std::mutex s_cache_mutex;

// HTTP response buffer
static char s_resp_buf[kMaxResponseSize + 1] = {0};
static int s_resp_len = 0;

static esp_err_t HttpEvent(esp_http_client_event_t* evt) {
    if (evt->event_id == HTTP_EVENT_ON_DATA) {
        int n = std::min(evt->data_len, kMaxResponseSize - s_resp_len);
        if (n > 0) {
            memcpy(s_resp_buf + s_resp_len, evt->data, n);
            s_resp_len += n;
        }
    }
    return ESP_OK;
}

/**
 * @brief Find the first occurrence of `needle` within [begin, end), or nullptr.
 * Bounded alternative to strstr() so key/value lookups cannot run past the
 * current entry's closing brace.
 */
static const char* FindWithin(const char* begin, const char* end, const char* needle) {
    const size_t needle_len = strlen(needle);
    for (const char* p = begin; end >= begin && p + needle_len <= end; ++p) {
        if (memcmp(p, needle, needle_len) == 0) return p;
    }
    return nullptr;
}

/**
 * @brief Parse JSON response and populate cache.
 * Expected: {"code":0, "holiday":{"2026-01-01":{"name":"X","rest":1},...}}
 */
static bool ParseResponse(int year, const char* json, int len, HolidayCache* cache) {
    if (!cache) return false;
    // Find "holiday" object
    const char* holiday_start = strstr(json, "\"holiday\"");
    if (!holiday_start) {
        ESP_LOGE(kTag, "No 'holiday' key in response");
        return false;
    }

    // Find the opening brace of holiday object
    const char* obj_start = strchr(holiday_start, '{');
    if (!obj_start) return false;

    cache->year = year;
    cache->entry_count = 0;

    // Scan for date entries: "YYYY-MM-DD":{...}
    char date_pattern[16];
    const char* pos = obj_start + 1;

    while (pos < json + len) {
        if (cache->entry_count >= kMaxHolidayEntries) {
            ESP_LOGW(kTag, "Holiday entries truncated at %d for %d", kMaxHolidayEntries, year);
            break;
        }

        // Find next date string "YYYY-MM-DD"
        snprintf(date_pattern, sizeof(date_pattern), "\"%04d-", year);
        const char* date_str = strstr(pos, date_pattern);
        if (!date_str || date_str >= json + len - 13) break;

        // Parse MM-DD. date_str points at the OPENING QUOTE of "YYYY-MM-DD",
        // so the month starts at +6 (after `"YYYY-`). Using +5 landed on the
        // '-' separator and made every entry fail to parse.
        int m = 0, d = 0;
        if (sscanf(date_str + 6, "%d-%d", &m, &d) != 2) {
            pos = date_str + 1;
            continue;
        }
        if (m < 1 || m > 12 || d < 1 || d > 31) {
            pos = date_str + 1;
            continue;
        }

        // Find the value object: {..."name":"...","rest":N...}
        const char* obj = strchr(date_str, '{');
        if (!obj || obj >= json + len) {
            pos = date_str + 1;
            continue;
        }
        // Bound all key/value lookups to this entry's closing brace so they
        // cannot pick up fields belonging to the next entry.
        const char* obj_end = strchr(obj, '}');
        if (!obj_end || obj_end >= json + len) {
            pos = date_str + 1;
            continue;
        }

        // Find "rest" value
        int is_rest = 1;  // default to rest
        const char* rest_key = FindWithin(obj, obj_end, "\"rest\"");
        if (rest_key) {
            const char* colon = FindWithin(rest_key, obj_end, ":");
            if (colon) {
                is_rest = atoi(colon + 1);
            }
        }

        // Find "name" value (stack buffer; no shared static state)
        char name_buf[32];
        name_buf[0] = '\0';
        const char* name_key = FindWithin(obj, obj_end, "\"name\"");
        if (name_key) {
            const char* colon = FindWithin(name_key, obj_end, ":");
            if (colon) {
                const char* q2 = FindWithin(colon, obj_end, "\"");
                if (q2) {
                    q2++;  // skip opening quote
                    const char* q3 = FindWithin(q2, obj_end, "\"");
                    if (q3 && q3 > q2) {
                        int nl = std::min((int)(q3 - q2), (int)sizeof(name_buf) - 1);
                        memcpy(name_buf, q2, nl);
                        name_buf[nl] = '\0';
                    }
                }
            }
        }

        // Skip entries that have no name and are normal rest days
        if (name_buf[0] == '\0' && is_rest) {
            pos = obj + 1;
            continue;
        }

        // Store entry
        HolidayEntry& e = cache->entries[cache->entry_count++];
        e.year = (int16_t)year;
        e.month = (int8_t)m;
        e.day = (int8_t)d;
        e.is_rest = (is_rest != 0);
        snprintf(e.name, sizeof(e.name), "%.15s", name_buf);

        pos = obj + 1;
    }

    ESP_LOGI(kTag, "Parsed %d holiday entries for %d", cache->entry_count, year);
    return true;
}

/**
 * @brief Save cache to NVS using raw NVS blob API.
 */
static void SaveCache(const HolidayCache& cache) {
    char key[32];
    snprintf(key, sizeof(key), "holiday_%d", cache.year);

    nvs_handle_t handle;
    esp_err_t err = nvs_open(kNvsNamespace, NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "NVS open failed: %s", esp_err_to_name(err));
        return;
    }

    // Serialize: entry_count (int) + entries array
    int blob_size = sizeof(int) + cache.entry_count * sizeof(HolidayEntry);
    if (blob_size > 4000) {  // NVS blob limit
        nvs_close(handle);
        return;
    }

    uint8_t blob[4096];
    memcpy(blob, &cache.entry_count, sizeof(int));
    if (cache.entry_count > 0) {
        memcpy(blob + sizeof(int), cache.entries,
               cache.entry_count * sizeof(HolidayEntry));
    }

    err = nvs_set_blob(handle, key, blob, blob_size);
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "NVS save failed: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(kTag, "Saved %d entries for %d to NVS", cache.entry_count, cache.year);
    }
    nvs_close(handle);
}

/**
 * @brief Load cache for a year from NVS.
 */
static bool LoadCache(int year, HolidayCache* cache) {
    if (!cache) return false;
    char key[32];
    snprintf(key, sizeof(key), "holiday_%d", year);

    nvs_handle_t handle;
    esp_err_t err = nvs_open(kNvsNamespace, NVS_READONLY, &handle);
    if (err != ESP_OK) {
        return false;
    }

    size_t blob_size = 0;
    err = nvs_get_blob(handle, key, nullptr, &blob_size);
    if (err != ESP_OK || blob_size < sizeof(int)) {
        nvs_close(handle);
        return false;
    }

    uint8_t blob[4096];
    if (blob_size > sizeof(blob)) {
        nvs_close(handle);
        return false;
    }

    err = nvs_get_blob(handle, key, blob, &blob_size);
    nvs_close(handle);
    if (err != ESP_OK) return false;

    int count = 0;
    memcpy(&count, blob, sizeof(int));
    if (count < 0 || count > kMaxHolidayEntries) return false;

    int expected = (int)sizeof(int) + count * (int)sizeof(HolidayEntry);
    if ((int)blob_size < expected) return false;

    cache->year = year;
    cache->entry_count = count;
    if (count > 0) {
        memcpy(cache->entries, blob + sizeof(int),
               count * sizeof(HolidayEntry));
    }

    ESP_LOGI(kTag, "Loaded %d cached entries for %d", count, year);
    return true;
}

static int FindCacheIndexLocked(int year) {
    // Caller must hold s_cache_mutex.
    for (int i = 0; i < kCacheSlots; ++i) {
        if (s_loaded[i] && s_caches[i].year == year) return i;
    }
    return -1;
}

static int AcquireCacheIndexLocked(int year) {
    // Caller must hold s_cache_mutex.
    const int existing = FindCacheIndexLocked(year);
    if (existing >= 0) return existing;

    for (int i = 0; i < kCacheSlots; ++i) {
        if (!s_loaded[i]) return i;
    }

    return s_caches[0].year <= s_caches[1].year ? 0 : 1;
}

// ============================================================
// Public API
// ============================================================

bool Init() {
    // Load current year's data from cache
    time_t now = time(nullptr);
    struct tm tm_buf;
    localtime_r(&now, &tm_buf);
    int year = tm_buf.tm_year + 1900;

    HolidayCache cache0 = {};
    HolidayCache cache1 = {};
    const bool loaded0 = LoadCache(year, &cache0);
    const bool loaded1 = LoadCache(year + 1, &cache1);

    {
        std::lock_guard<std::mutex> lock(s_cache_mutex);
        s_caches[0] = cache0;
        s_caches[1] = cache1;
        s_loaded[0] = loaded0;
        s_loaded[1] = loaded1;
    }

    return loaded0 || loaded1;
}

bool Fetch(int year) {
    // Before SNTP sync time() yields 1970; never burn a cache slot (or NVS)
    // on a garbage year. Safe to call again later once the clock is set.
    if (year < 2020) {
        ESP_LOGW(kTag, "Refusing to fetch holiday data for invalid year %d", year);
        return false;
    }

    s_resp_len = 0;
    memset(s_resp_buf, 0, sizeof(s_resp_buf));

    char url[80];
    snprintf(url, sizeof(url), "https://timor.tech/api/holiday/year/%d", year);
    ESP_LOGI(kTag, "Fetching holiday data: %s", url);

    esp_http_client_config_t config = {};
    config.url = url;
    config.method = HTTP_METHOD_GET;
    config.event_handler = HttpEvent;
    config.timeout_ms = 10000;
    config.disable_auto_redirect = false;
    config.crt_bundle_attach = esp_crt_bundle_attach;

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        ESP_LOGE(kTag, "Failed to init HTTP client");
        return false;
    }

    esp_err_t err = esp_http_client_perform(client);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "HTTP request failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return false;
    }

    int status = esp_http_client_get_status_code(client);
    (void)esp_http_client_get_content_length(client);  // logged by ESP_LOGI below
    esp_http_client_cleanup(client);

    if (status != 200) {
        ESP_LOGE(kTag, "HTTP status: %d", status);
        return false;
    }

    s_resp_buf[s_resp_len] = '\0';
    ESP_LOGI(kTag, "Received %d bytes", s_resp_len);

    HolidayCache parsed = {};
    if (!ParseResponse(year, s_resp_buf, s_resp_len, &parsed)) {
        ESP_LOGE(kTag, "Failed to parse response");
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(s_cache_mutex);
        const int cache_index = AcquireCacheIndexLocked(year);
        s_caches[cache_index] = parsed;
        s_loaded[cache_index] = true;
    }
    SaveCache(parsed);
    return true;
}

static bool FindEntry(int year, int month, int day, HolidayEntry* out) {
    // Copy the matching entry out under the lock so the UI thread never
    // holds (or dereferences) a pointer into a cache that Fetch() replaces.
    std::lock_guard<std::mutex> lock(s_cache_mutex);
    const int cache_index = FindCacheIndexLocked(year);
    if (cache_index < 0) return false;

    const HolidayCache& cache = s_caches[cache_index];
    for (int i = 0; i < cache.entry_count; i++) {
        const HolidayEntry& e = cache.entries[i];
        if (e.month == month && e.day == day) {
            *out = e;
            return true;
        }
    }
    return false;
}

bool IsHoliday(int year, int month, int day) {
    HolidayEntry e;
    return FindEntry(year, month, day, &e) && e.is_rest;
}

bool IsMakeupWorkday(int year, int month, int day) {
    HolidayEntry e;
    return FindEntry(year, month, day, &e) && !e.is_rest;
}

const char* GetHolidayName(int year, int month, int day) {
    HolidayEntry e;
    if (!FindEntry(year, month, day, &e)) {
        return nullptr;
    }
    if (!e.is_rest || e.name[0] == '\0') {
        return nullptr;
    }
    static char s_name_buf[sizeof(e.name)];
    snprintf(s_name_buf, sizeof(s_name_buf), "%s", e.name);
    return s_name_buf;
}

const char* GetMakeupLabel(int year, int month, int day) {
    HolidayEntry e;
    if (FindEntry(year, month, day, &e) && !e.is_rest) {
        return "班";
    }
    return nullptr;
}

}  // namespace holiday_fetcher
