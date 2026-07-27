/**
 * @file screenshot_renderer.h
 * @brief Generic screenshot/image board page renderer for rawdraw mode
 *
 * Displays a single full-screen image pushed from the NAS board service
 * (Playwright-rendered HTML → 2bpp dithered image). Acts as a generic
 * "board" surface: any rendered page (almanac/weather/news) can be pushed
 * here as a raw 2bpp/1bpp image and shown full-screen with no chrome.
 *
 * When no image has been pushed, shows a placeholder prompting the user
 * to generate content from the NAS web UI.
 *
 * Image data lifecycle:
 *   - SetImage() does malloc+memcpy; ownership transferred to this renderer
 *   - ClearImage() frees the buffer and reverts to placeholder
 *   - Destructor frees the buffer if still held
 */

#ifndef RAWDRAW_SCREENSHOT_RENDERER_H
#define RAWDRAW_SCREENSHOT_RENDERER_H

#include "page_renderer.h"
#include <cstdint>
#include <string>

namespace rawdraw {

class ScreenshotRenderer : public PageRenderer {
public:
    ScreenshotRenderer();
    ~ScreenshotRenderer() override;

    void Init(int width, int height) override;
    void Render(uint8_t* fb, int width, int height) override;
    bool HandleInput(const ButtonEvent& event) override;

    /**
     * @brief Cache a pushed image for full-screen display.
     *
     * Copies the raw image bytes (caller retains ownership of the input
     * buffer). Frees any previously cached image first.
     *
     * @param data Raw pixel data (2bpp packed or 1bpp packed)
     * @param size Byte length of data
     * @param w Image width in pixels
     * @param h Image height in pixels
     * @param is_2bpp true for 2bpp (BWRY) format, false for 1bpp mono
     * @param label Human-readable label (e.g. "老黄历") for status display
     */
    void SetImage(const uint8_t* data, uint32_t size, int w, int h,
                  bool is_2bpp, const std::string& label);

    /** Revert to placeholder. */
    void ClearImage();

    bool HasImage() const { return data_ != nullptr && size_ > 0; }
    const std::string& GetLabel() const { return label_; }

private:
    void FreeData();

    uint8_t* data_ = nullptr;
    uint32_t size_ = 0;
    int img_w_ = 0;
    int img_h_ = 0;
    bool is_2bpp_ = true;
    std::string label_;
};

}  // namespace rawdraw

#endif  // RAWDRAW_SCREENSHOT_RENDERER_H
