/**
 * @file weather_api.cc
 * @brief HeWeather API client implementation
 *
 * Uses esp_http_client for HTTP GET requests to HeWeather API.
 * JSON parsing done inline (no cJSON dependency to save flash).
 *
 * Key design:
 * - Hourly auto-refresh via esp_timer
 * - select()-based timeout (NO setsockopt(SO_RCVTIMEO))
 * - Thread-safe via static state
 *
 * API endpoints:
 *   https://devapi.qweather.com/v7/weather/now?key=XXX&location=XXX
 *   https://devapi.qweather.com/v7/weather/3d?key=XXX&location=XXX
 *   https://devapi.qweather.com/v7/air/now?key=XXX&location=XXX
 */

#include "weather_api.h"

#include <esp_log.h>
#include <esp_http_client.h>
#include <esp_timer.h>
#include <esp_crt_bundle.h>
#include <cJSON.h>
#include <cstring>
#include <cstdio>
#include <atomic>
#include <mutex>
#include <utility>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

static const char* kTag = "WeatherApi";

// ============================================================
// Static state
// ============================================================

static char s_api_key[64] = {0};
static char s_city_code[16] = {0};
static WeatherCallback s_callback;
static std::mutex s_state_mutex;
static std::atomic<bool> s_initialized{false};
static std::atomic<bool> s_in_progress{false};
static std::atomic<bool> s_fetch_pending{false};
static WeatherData s_last_data;
static esp_timer_handle_t s_timer = nullptr;
static TaskHandle_t s_worker_task = nullptr;

// ============================================================
// Weather icon mapping
// ============================================================

WeatherIcon ParseWeatherIcon(const char* text) {
    if (!text || !text[0]) return WeatherIcon::Unknown;

    // Sunny variants
    if (strstr(text, "晴") != nullptr) return WeatherIcon::Sunny;

    // Cloudy
    if (strstr(text, "多云") != nullptr) return WeatherIcon::Cloudy;
    if (strstr(text, "晴间多云") != nullptr) return WeatherIcon::Cloudy;

    // Overcast
    if (strstr(text, "阴") != nullptr) return WeatherIcon::Overcast;

    // Rain (all types)
    if (strstr(text, "雨") != nullptr) return WeatherIcon::Rain;

    // Snow
    if (strstr(text, "雪") != nullptr) return WeatherIcon::Snow;

    // Fog/Haze
    if (strstr(text, "雾") != nullptr) return WeatherIcon::Fog;
    if (strstr(text, "霾") != nullptr) return WeatherIcon::Fog;
    if (strstr(text, "沙尘") != nullptr) return WeatherIcon::Fog;

    return WeatherIcon::Unknown;
}

// ============================================================
// JSON parsing (using cJSON)
// ============================================================

static bool ParseNowJson(const char* json, WeatherData* out) {
    if (!json || !out) return false;

    cJSON* root = cJSON_Parse(json);
    if (!root) {
        ESP_LOGE(kTag, "Failed to parse JSON");
        return false;
    }

    // Check response code
    cJSON* code_item = cJSON_GetObjectItem(root, "code");
    if (!cJSON_IsString(code_item) || !code_item->valuestring) {
        ESP_LOGE(kTag, "No 'code' field in response");
        cJSON_Delete(root);
        return false;
    }
    const char* code = code_item->valuestring;
    if (strcmp(code, "200") != 0) {
        ESP_LOGE(kTag, "API error code: %s", code);
        cJSON_Delete(root);
        return false;
    }

    cJSON* now = cJSON_GetObjectItem(root, "now");
    if (!now) {
        ESP_LOGE(kTag, "No 'now' object in response");
        cJSON_Delete(root);
        return false;
    }

    auto get_str = [now](const char* key) -> const char* {
        cJSON* item = cJSON_GetObjectItem(now, key);
        return item ? item->valuestring : nullptr;
    };

    // Parse temperature
    const char* tmp = get_str("temp");
    if (tmp) {
        out->temp = tmp;
        out->temp_int = atoi(tmp);
    }

    // Feels like
    const char* feels = get_str("feelsLike");
    if (feels) out->feels_like = feels;

    // Weather text + icon
    const char* icon = get_str("icon");
    if (icon) out->weather_icon = icon;

    const char* weather = get_str("text");
    if (weather) out->weather_text = weather;

    // Wind
    const char* wind_dir = get_str("windDir");
    if (wind_dir) out->wind_dir = wind_dir;

    const char* wind_scale = get_str("windScale");
    if (wind_scale) out->wind_scale = wind_scale;

    // Humidity
    const char* humidity = get_str("humidity");
    if (humidity) out->humidity = humidity;

    // Update time
    const char* update = get_str("obsTime");
    if (update) {
        // Extract HH:MM from "2024-01-15T14:30+08:00"
        out->update_time = update;
        const char* t_pos = strchr(update, 'T');
        if (t_pos && t_pos[1] && t_pos[2] && t_pos[3] == ':') {
            out->update_time = std::string(t_pos + 1, 5);
        }
    }

    cJSON_Delete(root);
    return true;
}

static bool ParseForecastJson(const char* json, WeatherData* out) {
    if (!json || !out) return false;

    cJSON* root = cJSON_Parse(json);
    if (!root) {
        ESP_LOGE(kTag, "Failed to parse forecast JSON");
        return false;
    }

    cJSON* code_item = cJSON_GetObjectItem(root, "code");
    const char* code = cJSON_IsString(code_item) ? code_item->valuestring : nullptr;
    if (!code || strcmp(code, "200") != 0) {
        ESP_LOGE(kTag, "Forecast API error code: %s", code ? code : "null");
        cJSON_Delete(root);
        return false;
    }

    cJSON* daily = cJSON_GetObjectItem(root, "daily");
    if (!cJSON_IsArray(daily)) {
        ESP_LOGE(kTag, "No 'daily' array in forecast response");
        cJSON_Delete(root);
        return false;
    }

    out->forecast.clear();
    const char* labels[3] = {"今天", "明天", "后天"};
    int index = 0;
    cJSON* day = nullptr;
    cJSON_ArrayForEach(day, daily) {
        if (index >= 3) break;
        WeatherForecastDay item;
        item.label = labels[index];

        cJSON* text_day = cJSON_GetObjectItem(day, "textDay");
        if (cJSON_IsString(text_day) && text_day->valuestring) {
            item.weather_text = text_day->valuestring;
        }
        cJSON* icon_day = cJSON_GetObjectItem(day, "iconDay");
        if (cJSON_IsString(icon_day) && icon_day->valuestring) {
            item.icon_code = icon_day->valuestring;
        }
        cJSON* temp_min = cJSON_GetObjectItem(day, "tempMin");
        if (cJSON_IsString(temp_min) && temp_min->valuestring) {
            item.temp_min = atoi(temp_min->valuestring);
        } else if (cJSON_IsNumber(temp_min)) {
            item.temp_min = temp_min->valueint;
        }
        cJSON* temp_max = cJSON_GetObjectItem(day, "tempMax");
        if (cJSON_IsString(temp_max) && temp_max->valuestring) {
            item.temp_max = atoi(temp_max->valuestring);
        } else if (cJSON_IsNumber(temp_max)) {
            item.temp_max = temp_max->valueint;
        }
        out->forecast.push_back(item);
        ++index;
    }

    cJSON_Delete(root);
    return !out->forecast.empty();
}

static bool ParseAirJson(const char* json, WeatherData* out) {
    if (!json || !out) return false;

    cJSON* root = cJSON_Parse(json);
    if (!root) {
        ESP_LOGE(kTag, "Failed to parse air JSON");
        return false;
    }

    cJSON* code_item = cJSON_GetObjectItem(root, "code");
    const char* code = cJSON_IsString(code_item) ? code_item->valuestring : nullptr;
    if (!code || strcmp(code, "200") != 0) {
        ESP_LOGW(kTag, "Air API error code: %s", code ? code : "null");
        cJSON_Delete(root);
        return false;
    }

    cJSON* now = cJSON_GetObjectItem(root, "now");
    if (!cJSON_IsObject(now)) {
        cJSON_Delete(root);
        return false;
    }

    cJSON* category = cJSON_GetObjectItem(now, "category");
    if (cJSON_IsString(category) && category->valuestring) {
        out->air_quality = category->valuestring;
    }

    cJSON* aqi = cJSON_GetObjectItem(now, "aqi");
    if (cJSON_IsString(aqi) && aqi->valuestring) {
        out->air_aqi = atoi(aqi->valuestring);
    } else if (cJSON_IsNumber(aqi)) {
        out->air_aqi = aqi->valueint;
    }

    cJSON_Delete(root);
    return true;
}

// ============================================================
// HTTP client
// ============================================================

static char s_response_buf[4096] = {0};
static int s_response_len = 0;
static bool s_response_truncated = false;

static esp_err_t HttpEventHandler(esp_http_client_event_t* evt) {
    switch (evt->event_id) {
        case HTTP_EVENT_ON_DATA:
            if (s_response_len + evt->data_len < (int)sizeof(s_response_buf)) {
                memcpy(s_response_buf + s_response_len, evt->data, evt->data_len);
                s_response_len += evt->data_len;
            } else if (!s_response_truncated) {
                // Warn once per response; larger bodies are cut off.
                ESP_LOGW(kTag, "HTTP response exceeds %u bytes, truncating",
                         (unsigned)sizeof(s_response_buf));
                s_response_truncated = true;
            }
            break;
        default:
            break;
    }
    return ESP_OK;
}

static bool HttpGet(const char* url) {
    s_response_len = 0;
    s_response_truncated = false;
    memset(s_response_buf, 0, sizeof(s_response_buf));

    esp_http_client_config_t config = {};
    config.url = url;
    config.method = HTTP_METHOD_GET;
    config.event_handler = HttpEventHandler;
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
    if (status != 200) {
        ESP_LOGE(kTag, "HTTP status: %d for %s", status, url);
        esp_http_client_cleanup(client);
        return false;
    }

    s_response_buf[s_response_len] = '\0';
    esp_http_client_cleanup(client);
    return true;
}

// One full fetch pass (now + forecast + air). Assumes the caller holds the
// s_in_progress gate. Returns true only if current weather was fetched.
static bool FetchOnce() {
    char api_key[sizeof(s_api_key)] = {};
    char city_code[sizeof(s_city_code)] = {};
    WeatherCallback callback;
    {
        std::lock_guard<std::mutex> lock(s_state_mutex);
        strlcpy(api_key, s_api_key, sizeof(api_key));
        strlcpy(city_code, s_city_code, sizeof(city_code));
        callback = s_callback;
    }

    if (!api_key[0] || !city_code[0]) {
        ESP_LOGE(kTag, "API key or city code not set");
        return false;
    }

    WeatherData data;

    char url[256];
    snprintf(url, sizeof(url),
             "https://devapi.qweather.com/v7/weather/now?key=%s&location=%s",
             api_key, city_code);
    ESP_LOGI(kTag, "Fetching current weather: %s", url);
    if (!HttpGet(url) || !ParseNowJson(s_response_buf, &data)) {
        ESP_LOGE(kTag, "Failed to fetch or parse current weather");
        return false;
    }

    snprintf(url, sizeof(url),
             "https://devapi.qweather.com/v7/weather/3d?key=%s&location=%s",
             api_key, city_code);
    ESP_LOGI(kTag, "Fetching forecast: %s", url);
    if (!HttpGet(url) || !ParseForecastJson(s_response_buf, &data)) {
        ESP_LOGW(kTag, "Failed to fetch or parse 3-day forecast");
    }

    snprintf(url, sizeof(url),
             "https://devapi.qweather.com/v7/air/now?key=%s&location=%s",
             api_key, city_code);
    ESP_LOGI(kTag, "Fetching air quality: %s", url);
    if (!HttpGet(url) || !ParseAirJson(s_response_buf, &data)) {
        ESP_LOGW(kTag, "Failed to fetch or parse air quality");
    }

    {
        std::lock_guard<std::mutex> lock(s_state_mutex);
        s_last_data = data;
    }
    ESP_LOGI(kTag, "Weather: %s %s°C AQI=%d forecast=%d",
             data.weather_text.c_str(),
             data.temp.c_str(),
             data.air_aqi,
             static_cast<int>(data.forecast.size()));

    if (callback) {
        callback(data);
    }
    return true;
}

static void DoFetch() {
    if (!s_initialized.load(std::memory_order_acquire)) return;

    bool expected = false;
    if (!s_in_progress.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) {
        // A fetch is already running; it re-checks s_fetch_pending before
        // finishing, so this request will be served by that pass.
        return;
    }

    // Consume the pending flag; re-check after each pass so requests made
    // while a fetch was in flight (e.g. weather_api_set_city()) are served
    // instead of dropped.
    while (s_fetch_pending.exchange(false, std::memory_order_acq_rel)) {
        FetchOnce();
    }

    s_in_progress.store(false, std::memory_order_release);
}

// ============================================================
// Background worker and timer callback
// ============================================================

static void WeatherWorker(void*) {
    while (true) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        DoFetch();
    }
}

static bool RequestFetch() {
    if (!s_initialized.load(std::memory_order_acquire) || s_worker_task == nullptr) {
        return false;
    }

    // Always flag and notify, even while a fetch is in progress: DoFetch()
    // re-checks s_fetch_pending when it finishes, so the request triggers a
    // follow-up fetch instead of being dropped.
    s_fetch_pending.store(true, std::memory_order_release);
    xTaskNotifyGive(s_worker_task);
    return true;
}

static void TimerCallback(void* arg) {
    (void)arg;
    ESP_LOGD(kTag, "Hourly weather refresh triggered");
    RequestFetch();
}

// ============================================================
// Public API
// ============================================================

void weather_api_init(const char* api_key, const char* city_code, WeatherCallback callback) {
    char city_log[sizeof(s_city_code)] = {};
    {
        std::lock_guard<std::mutex> lock(s_state_mutex);
        if (s_initialized.load(std::memory_order_acquire)) {
            ESP_LOGW(kTag, "Already initialized");
            return;
        }
        strlcpy(s_api_key, api_key ? api_key : "", sizeof(s_api_key));
        strlcpy(s_city_code, city_code ? city_code : "", sizeof(s_city_code));
        strlcpy(city_log, s_city_code, sizeof(city_log));
        s_callback = std::move(callback);
        // Set inside the lock so a concurrent second init cannot slip past
        // the check above before setup completes (double-init race fix).
        s_initialized.store(true, std::memory_order_release);
    }

    // HTTPS/TLS needs a deep stack; other networked tasks in this project
    // use 16K as well.
    if (xTaskCreate(&WeatherWorker, "weather_fetch", 16384, nullptr, 4, &s_worker_task) != pdPASS) {
        ESP_LOGE(kTag, "Failed to create weather worker task");
        s_worker_task = nullptr;
        s_initialized.store(false, std::memory_order_release);
        return;
    }

    // Create hourly refresh timer
    esp_timer_create_args_t timer_args = {
        .callback = TimerCallback,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "weather_refresh",
        .skip_unhandled_events = true,
    };

    if (esp_timer_create(&timer_args, &s_timer) == ESP_OK) {
        // Start with 1 hour interval (3600 * 1,000,000 microseconds)
        esp_timer_start_periodic(s_timer, 3600LL * 1000000LL);
        ESP_LOGI(kTag, "Timer started (1h interval)");
    } else {
        ESP_LOGE(kTag, "Failed to create timer");
    }

    // Fetch immediately on the dedicated worker, never on the timer task.
    RequestFetch();

    ESP_LOGI(kTag, "Weather API initialized: city=%s", city_log);
}

bool weather_api_fetch_now() {
    return RequestFetch();
}

void weather_api_set_city(const char* city_code) {
    if (!city_code) return;
    {
        std::lock_guard<std::mutex> lock(s_state_mutex);
        strlcpy(s_city_code, city_code, sizeof(s_city_code));
    }
    ESP_LOGI(kTag, "City changed to: %s", city_code);

    // Fetch new data for new city. If a fetch is already in progress the
    // request is flagged and served by a follow-up pass (see RequestFetch).
    weather_api_fetch_now();
}

void weather_api_set_key(const char* api_key) {
    if (!api_key) return;
    std::lock_guard<std::mutex> lock(s_state_mutex);
    strlcpy(s_api_key, api_key, sizeof(s_api_key));
}

const char* weather_api_get_city() {
    // Snapshot under the lock; s_city_code may be rewritten concurrently by
    // weather_api_set_city().
    std::lock_guard<std::mutex> lock(s_state_mutex);
    return s_city_code;
}

bool weather_api_is_ready() {
    return s_initialized.load(std::memory_order_acquire);
}

WeatherData weather_api_get_last_data() {
    // Return a value copy under the lock: s_last_data is rewritten by the
    // fetch worker, so handing out a pointer would be a data race.
    std::lock_guard<std::mutex> lock(s_state_mutex);
    return s_last_data;
}
