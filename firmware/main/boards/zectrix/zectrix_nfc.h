#ifndef ZECTRIX_NFC_H
#define ZECTRIX_NFC_H

#include <driver/gpio.h>
#include <driver/i2c_master.h>
#include <esp_err.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "boards/common/i2c_bus_lock.h"
#include "boards/common/i2c_device.h"

class ZectrixNfc : public I2cDevice {
public:
    static constexpr size_t kBlockSize = 16;
    static constexpr uint8_t kUidBlockAddress = 0x00;
    static constexpr uint8_t kUserDataStartBlock = 0x01;
    static constexpr uint8_t kUserDataEndBlock = 0x37;
    // KNOWN OVERLAP RISK: 0x04 lies INSIDE the user-data block range
    // [0x01, 0x37], because the real chip memory layout is unconfirmed.
    // Capacities are intentionally left unchanged: command-area accesses
    // (kCommandBlockAddress..kUserDataEndBlock) and user-data accesses
    // (kUserDataStartBlock..) alias the same blocks — user data at offset
    // >= (0x04 - 0x01) * kBlockSize = 48 clobbers the command area and
    // vice versa. Do not write both regions concurrently.
    static constexpr uint8_t kCommandBlockAddress = 0x04;
    static constexpr size_t kUserDataCapacity =
        (kUserDataEndBlock - kUserDataStartBlock + 1) * kBlockSize;
    static constexpr size_t kCommandAreaCapacity =
        (kUserDataEndBlock - kCommandBlockAddress + 1) * kBlockSize;

    ZectrixNfc(i2c_master_bus_handle_t i2c_bus,
               uint8_t addr,
               gpio_num_t power_gpio,
               gpio_num_t fd_gpio,
               int fd_active_level);
    ~ZectrixNfc();

    bool Init();
    bool PowerOn();
    void PowerOff();
    bool IsPowered() const;
    bool HasField() const;
    void SetFieldCallback(std::function<void(bool)> callback);

    esp_err_t ReadBlock(uint8_t block_addr, uint8_t out[kBlockSize]);
    esp_err_t WriteBlock(uint8_t block_addr, const uint8_t data[kBlockSize]);
    esp_err_t ReadUserData(uint16_t offset, uint8_t* out, size_t len);
    esp_err_t WriteUserData(uint16_t offset, const uint8_t* data, size_t len);
    esp_err_t ClearUserData(uint16_t offset, size_t len);
    esp_err_t ReadCommandArea(uint8_t* out, size_t len);
    esp_err_t ClearCommandArea(size_t len);
    esp_err_t ReadUid(std::array<uint8_t, 7>* uid);
    size_t GetUserDataCapacity() const;
    esp_err_t ReadNdef(std::vector<uint8_t>* message);
    esp_err_t WriteNdef(const std::vector<uint8_t>& message);
    esp_err_t WriteTextNdef(const std::string& text, const std::string& language = "zh");
    esp_err_t WriteUriNdef(const std::string& uri);
    static std::vector<uint8_t> BuildUriNdefMessage(const std::string& uri);

private:
    static void FieldTaskEntry(void* arg);
    static void FieldIsrHandler(void* arg);

    static esp_err_t EnsureIsrServiceInstalled();

    // Removes the FD ISR handler and stops/joins field_task_ so no callback
    // or task can touch a freed object. Idempotent; used by Init() failure
    // paths and the destructor.
    void TeardownFieldMonitoring();

    esp_err_t Probe();
    esp_err_t BeginTransferSessionLocked(const char* reason);
    void EndTransferSessionLocked();
    esp_err_t ExecuteTransferLocked(const char* reason, const std::function<esp_err_t()>& op);
    esp_err_t ReadBlockInternal(uint8_t block_addr, uint8_t out[kBlockSize]);
    esp_err_t WriteBlockInternal(uint8_t block_addr, const uint8_t* data, size_t len);
    esp_err_t EnsureReadyForTransferLocked();
    bool IsValidUserRange(uint16_t offset, size_t len) const;
    bool IsValidCommandRange(size_t len) const;
    bool IsFieldLevelActive() const;
    void UpdateFieldState(bool field_present, bool invoke_callback);
    void DispatchFieldState(bool field_present);
    void FieldTask();

    gpio_num_t power_gpio_ = GPIO_NUM_NC;
    gpio_num_t fd_gpio_ = GPIO_NUM_NC;
    int fd_active_level_ = 1;
    TaskHandle_t field_task_ = nullptr;
    std::atomic<bool> initialized_{false};
    std::atomic<bool> powered_{false};
    std::atomic<bool> field_present_{false};
    // FieldTask shutdown handshake: done is set by the owner to request exit,
    // exited is set by the task right before vTaskDelete(NULL).
    std::atomic<bool> field_task_done_{false};
    std::atomic<bool> field_task_exited_{false};
    std::mutex mutex_;
    std::unique_ptr<ScopedI2cBusLock> i2c_session_lock_;
    std::function<void(bool)> field_callback_;
};

#endif  // ZECTRIX_NFC_H
