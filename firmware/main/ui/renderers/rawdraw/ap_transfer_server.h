/**
 * @file ap_transfer_server.h
 * @brief WiFi AP + HTTP Server for image transfer
 *
 * Provides:
 * - WiFi AP (SSID: InkScreen-AP, Password: 12345678)
 * - HTTP Server at 192.168.4.1
 * - HTML page for image upload
 * - Floyd-Steinberg dithering
 * - Save to SPIFFS
 */

#ifndef AP_TRANSFER_SERVER_H
#define AP_TRANSFER_SERVER_H

#include <string>
#include <atomic>
#include <functional>
#include <mutex>
#include <esp_http_server.h>
#include <esp_netif.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace rawdraw {

/**
 * @brief AP Transfer Server for WiFi image upload
 */
class ApTransferServer {
public:
    ApTransferServer();
    ~ApTransferServer();

    // Start AP + HTTP server
    void Start();

    // Start HTTP server on the current Wi-Fi station interface
    bool StartLan(const std::string& ip_address);
    
    // Stop AP + HTTP server
    void Stop();

    // Check if server is running
    bool IsRunning() const { return running_.load(); }
    bool IsApMode() const { return mode_.load() == TransferMode::kAp; }
    bool IsLanMode() const { return mode_.load() == TransferMode::kLan; }

    // State callback
    enum ServerState {
        kStopped,
        kApStarted,
        kClientConnected,
        kReceivingImage,
        kProcessingImage,
        kImageSaved,
        kError,
    };
    
    void SetStateCallback(std::function<void(ServerState, const std::string&)> callback);

    // Image received callback (called when image saved to SPIFFS)
    void SetImageReceivedCallback(std::function<void(const char* photo_id)> callback);

    void SetSettingsChangedCallback(std::function<void(int slideshow_interval_minutes)> callback);
    void SetPhotosChangedCallback(std::function<void()> callback);
    void SetShowPhotoCallback(std::function<bool(const std::string& photo_id)> callback);

    // Page switching (web remote-control): web UI calls /page/show to switch the
    // device screen to a given page (gallery / weather / calendar / ...).
    // callback returns true if the page id was recognised and switched to.
    void SetSwitchPageCallback(std::function<bool(const std::string& page_id)> callback);
    // Returns a JSON array of available pages, e.g.
    // [{"id":"gallery","name":"相册"},...].  Used by the web UI to render the
    // control panel dynamically.
    void SetPageListCallback(std::function<std::string()> callback);

    // Board pipeline: NAS pushes a rendered 2bpp/1bpp image for the generic
    // Screenshot page. The callback receives the human-readable label (e.g.
    // "老黄历") plus the raw pixel bytes and dimensions. Returns true on
    // acceptance. Wired by RawDrawUiManager to ScreenshotRenderer::SetImage.
    using ScreenshotCallback = std::function<bool(const std::string& label,
                                                  const uint8_t* data, uint32_t size,
                                                  int w, int h, bool is_2bpp)>;
    void SetScreenshotCallback(ScreenshotCallback callback);

    // LifeBar birth date (from the NAS web panel). The callback persists
    // to NVS and refreshes the LifeBar page if visible.
    using LifeBarBirthCallback = std::function<bool(int y, int m, int d)>;
    void SetLifeBarBirthCallback(LifeBarBirthCallback callback);

private:
    enum class TransferMode {
        kNone,
        kAp,
        kLan,
    };

    httpd_handle_t server_ = nullptr;
    esp_netif_t* ap_netif_ = nullptr;
    std::mutex lifecycle_mutex_;
    std::atomic<bool> running_{false};
    std::atomic<bool> starting_{false};
    std::atomic<bool> cancel_start_{false};
    SemaphoreHandle_t start_complete_ = nullptr;
    std::string ap_ip_ = "192.168.4.1";
    std::atomic<TransferMode> mode_{TransferMode::kNone};

    std::function<void(ServerState, const std::string&)> state_callback_;
    std::function<void(const char* photo_id)> image_received_callback_;
    std::function<void(int slideshow_interval_minutes)> settings_changed_callback_;
    std::function<void()> photos_changed_callback_;
    std::function<bool(const std::string& photo_id)> show_photo_callback_;
    std::function<bool(const std::string& page_id)> switch_page_callback_;
    std::function<std::string()> page_list_callback_;  // returns JSON array string
    ScreenshotCallback screenshot_callback_;
    LifeBarBirthCallback lifebar_birth_callback_;

    bool StartAccessPoint();
    const std::string& GetApIp() const { return ap_ip_; }
    bool StartHttpServer();
    static void StartTask(void* arg);

    // HTTP handlers
    static esp_err_t IndexHandler(httpd_req_t* req);
    static esp_err_t UploadHandler(httpd_req_t* req);
    static esp_err_t StatusHandler(httpd_req_t* req);
    static esp_err_t SettingsHandler(httpd_req_t* req);
    static esp_err_t PhotosHandler(httpd_req_t* req);
    static esp_err_t PhotoHandler(httpd_req_t* req);
    static esp_err_t PhotoMetaHandler(httpd_req_t* req);
    static esp_err_t PhotoMoveHandler(httpd_req_t* req);
    static esp_err_t PhotoShowHandler(httpd_req_t* req);
    static esp_err_t PageShowHandler(httpd_req_t* req);
    static esp_err_t ScreenshotSetHandler(httpd_req_t* req);
    static esp_err_t LifeBarBirthHandler(httpd_req_t* req);
    static esp_err_t PageListHandler(httpd_req_t* req);
    
    // Notify state change
    void NotifyState(ServerState state, const std::string& message);
    
};

}  // namespace rawdraw

#endif  // AP_TRANSFER_SERVER_H
