#pragma once

#include <driver/gpio.h>
#include <functional>
#include <atomic>
#include <cstdint>
#include <mutex>

class ChargeStatus {
public:
    enum class State : uint8_t {
        kNoPower = 0,
        kCharging = 1,
        kFull = 2,
        kNoBattery = 3,
    };

    struct Snapshot {
        State state;
        bool power_present;
        bool charging;   // UI/LED charging indicator (includes no-battery)
        bool full;
        bool no_battery;
    };

    void Init(gpio_num_t detect_gpio, gpio_num_t full_gpio, int64_t now_ms);
    void Tick(int64_t now_ms);
    Snapshot Get() const;
    void OnStateChanged(std::function<void(const Snapshot&)> cb);

private:
    // Runs the state machine under state_mutex_. Returns true when the
    // published snapshot changed; cb (may be empty) receives the registered
    // callback so the caller can invoke it OUTSIDE the lock.
    bool TickLocked(int64_t now_ms, std::function<void(const Snapshot&)>& cb);
    void UpdateSnapshotLocked(State state, bool power_present, bool full, bool no_battery);
    static uint32_t Pack(State state, bool power_present, bool charging, bool full, bool no_battery);
    static Snapshot Unpack(uint32_t v);

    gpio_num_t detect_gpio_ = GPIO_NUM_NC;
    gpio_num_t full_gpio_ = GPIO_NUM_NC;

    // State-machine fields below are shared between Tick() callers (board
    // task, power LED task) and guarded by state_mutex_.
    int64_t detect_high_start_ms_ = -1;
    int64_t full_high_start_ms_ = -1;
    int64_t last_detect_seen_ms_ = -1;
    int64_t last_full_seen_ms_ = -1;
    int64_t last_power_present_ms_ = -1;

    // recursive_mutex: Init() holds the lock and calls TickLocked(), which
    // locks again on its own.
    std::recursive_mutex state_mutex_;
    std::atomic<bool> initialized_{false};
    std::atomic<uint32_t> snapshot_{0};
    std::function<void(const Snapshot&)> on_state_changed_;

    static constexpr int kPowerPresentHoldMs = 1000;
    static constexpr int kStableHighMs = 400;
    static constexpr int kAltWindowMs = 1500;
};
