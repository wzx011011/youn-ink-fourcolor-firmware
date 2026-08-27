#include <esp_log.h>
#include <esp_err.h>
#include <nvs.h>
#include <nvs_flash.h>
#include <driver/gpio.h>
#include <esp_event.h>
#include <esp_sleep.h>
#include <esp_system.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "application.h"
#include "system_info.h"

#define TAG "main"

// Persists across soft resets and deep sleep, cleared only on power-on.
// Used to avoid bouncing more than once per software reset.
static RTC_DATA_ATTR bool s_sw_reset_bounced = false;

static void LogNvsStats() {
    nvs_stats_t stats = {};
    esp_err_t ret = nvs_get_stats(nullptr, &stats);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "NVS stats read failed: %s", esp_err_to_name(ret));
        return;
    }
    uint32_t used = stats.used_entries;
    uint32_t total = stats.total_entries;
    uint32_t free_entries = stats.free_entries;
    uint32_t percent = (total > 0) ? (used * 100U / total) : 0;
    ESP_LOGI(TAG, "NVS stats: used=%u free=%u total=%u (%u%%) namespaces=%u",
             used, free_entries, total, percent, stats.namespace_count);
}

extern "C" void app_main(void)
{
    // OTA rollback is enabled in sdkconfig. Without this call the bootloader
    // treats the freshly-flashed app as "pending verify" and silently rolls
    // back to the other slot after a few reboots — the running firmware then
    // reverts to an old build with no visible error. Mark this app valid as
    // the very first thing we do.
#if CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
    {
        const esp_partition_t *running = esp_ota_get_running_partition();
        if (running) {
            esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
            if (esp_ota_get_state_partition(running, &state) == ESP_OK &&
                state != ESP_OTA_IMG_VALID) {
                esp_err_t ota_err = esp_ota_mark_app_valid_cancel_rollback();
                ESP_LOGI(TAG, "OTA app marked valid (%s)", esp_err_to_name(ota_err));
            }
        }
    }
#endif

    // Some soft/external reset paths leave the Wi-Fi RF state dirty until the
    // next hardware-equivalent reset. For those reset reasons only, perform a
    // brief deep-sleep round-trip once to come back with clean radio state.
    {
        const auto reason = esp_reset_reason();
        ESP_LOGI(TAG, "Boot reset reason=%d bounced=%d", reason, s_sw_reset_bounced ? 1 : 0);
        // Keep this narrowly scoped. External reset after flashing should boot
        // like a normal hardware reset; bouncing that path through deep sleep
        // has proven flaky on this board. Only software-reset paths still get
        // the one-shot deep-sleep bounce.
        const bool need_bounce = !s_sw_reset_bounced &&
                                 (reason == ESP_RST_SW);
        if (need_bounce) {
            ESP_LOGI(TAG, "Reset reason %d — bouncing via deep sleep for clean Wi-Fi init", reason);
            s_sw_reset_bounced = true;
            esp_sleep_enable_timer_wakeup(500000ULL);  // 500 ms
            esp_deep_sleep_start();
        }
        s_sw_reset_bounced = false;
    }  // clear for next time

    // Initialize NVS flash for WiFi configuration
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "Erasing NVS flash to fix corruption");
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
    LogNvsStats();

    auto& app = Application::GetInstance();
    app.Initialize();
    app.Run();  // This function runs the main event loop and never returns
}
