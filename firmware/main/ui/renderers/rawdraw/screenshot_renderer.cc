/**
 * @file screenshot_renderer.cc
 * @brief Generic screenshot/image board page renderer implementation
 *
 * Full-screen image rendering loop adapted from PhotoGalleryRenderer's
 * fullscreen mode (photo_gallery.cc RenderFullscreenMode). Supports both
 * 2bpp BWRY and 1bpp mono packed formats.
 */

#include "screenshot_renderer.h"
#include "rawdraw/rawdraw.h"
#include "rawdraw/theme.h"
#include <algorithm>
#include <cstring>

// External font for placeholder text
extern const lv_font_t SourceHanSansSC_Regular_slim;
extern const lv_font_t SourceHanSansSC_Medium_slim;

namespace rawdraw {

namespace {

// --- Packed image byte-decode helpers (mirrors photo_gallery.cc statics) ---
// Duplicated here to avoid touching photo_gallery.cc. These are tiny and
// format-stable; keeping a private copy is cleaner than refactoring shared
// internals across two unrelated renderers.

inline int BytesPerRow1bpp(int width) {
    return std::max(1, (width + 7) / 8);
}

inline int BytesPerRow2bpp(int width) {
    return std::max(1, (width + 3) / 4);
}

inline bool IsBwry2bppImage(int width, int height, uint32_t size) {
    return width > 0 && height > 0 &&
           size >= static_cast<uint32_t>(BytesPerRow2bpp(width) * height);
}

inline bool IsMono1bppImage(int width, int height, uint32_t size) {
    return width > 0 && height > 0 &&
           size >= static_cast<uint32_t>(BytesPerRow1bpp(width) * height);
}

// Decode one pixel from packed image data into a display Color.
// 2bpp layout: 4 px/byte, MSB first; color index 0..3 maps to Color enum.
// 1bpp layout: 8 px/byte, MSB first; 1=white, 0=black.
inline Color ReadPixelColor(const uint8_t* data, uint32_t size, int img_w,
                            bool bwry2bpp, int x, int y) {
    if (!data || img_w <= 0 || x < 0 || y < 0) return BLACK;
    if (bwry2bpp) {
        const int bpr = BytesPerRow2bpp(img_w);
        const int offset = y * bpr + (x >> 2);
        if (offset < 0 || offset >= static_cast<int>(size)) return BLACK;
        const int shift = 6 - ((x & 0x03) * 2);
        const uint8_t color = (data[offset] >> shift) & 0x03;
        return static_cast<Color>(color);
    }
    const int bpr = BytesPerRow1bpp(img_w);
    const int offset = y * bpr + (x >> 3);
    if (offset < 0 || offset >= static_cast<int>(size)) return BLACK;
    const int bit = 7 - (x & 0x07);
    return ((data[offset] >> bit) & 0x01) != 0 ? WHITE : BLACK;
}

}  // namespace

ScreenshotRenderer::ScreenshotRenderer() = default;

ScreenshotRenderer::~ScreenshotRenderer() {
    FreeData();
}

void ScreenshotRenderer::FreeData() {
    if (data_) {
        free(data_);
        data_ = nullptr;
    }
    size_ = 0;
    img_w_ = 0;
    img_h_ = 0;
}

void ScreenshotRenderer::Init(int width, int height) {
    width_ = width;
    height_ = height;
    needs_full_refresh_ = true;
}

void ScreenshotRenderer::SetImage(const uint8_t* data, uint32_t size, int w, int h,
                                  bool is_2bpp, const std::string& label) {
    FreeData();
    if (!data || size == 0 || w <= 0 || h <= 0) return;
    uint8_t* buf = static_cast<uint8_t*>(malloc(size));
    if (!buf) return;
    memcpy(buf, data, size);
    data_ = buf;
    size_ = size;
    img_w_ = w;
    img_h_ = h;
    is_2bpp_ = is_2bpp;
    label_ = label;
    MarkFullRefresh();
}

void ScreenshotRenderer::ClearImage() {
    FreeData();
    label_.clear();
    MarkFullRefresh();
}

void ScreenshotRenderer::Render(uint8_t* fb, int width, int height) {
    if (!fb) return;

    const auto& theme = ThemeManager::Get();

    // No image: show placeholder
    if (!data_ || size_ == 0) {
        DrawStyledRect(fb, width, {0, 0, width, height},
                       theme.Style(ThemeToken::BackgroundPrimary));
        const lv_font_t* font = &SourceHanSansSC_Medium_slim;
        const lv_font_t* sub_font = &SourceHanSansSC_Regular_slim;
        const Color accent = theme.ColorFor(ThemeToken::Accent);
        const Color secondary = theme.ColorFor(ThemeToken::TextSecondary);

        const char* title = "看板";
        int tw = MeasureTextWidth(title, font);
        DrawText(fb, width, ((width - tw) / 2 + 7) & ~7, height / 2 - font->line_height,
                 title, font, accent);

        const char* hint1 = "暂无看板内容";
        int hw1 = MeasureTextWidth(hint1, sub_font);
        DrawText(fb, width, ((width - hw1) / 2 + 7) & ~7, height / 2 + 4,
                 hint1, sub_font, secondary);

        const char* hint2 = "请在 NAS 网页「看板」生成";
        int hw2 = MeasureTextWidth(hint2, sub_font);
        DrawText(fb, width, ((width - hw2) / 2 + 7) & ~7,
                 height / 2 + 4 + sub_font->line_height + 4,
                 hint2, sub_font, secondary);
        return;
    }

    // Have image: clear to white and blit full-screen.
    DrawStyledRect(fb, width, {0, 0, width, height},
                   theme.Style(ThemeToken::BackgroundPrimary));

    const bool bwry2bpp = is_2bpp_ &&
                          IsBwry2bppImage(img_w_, img_h_, size_);
    const int photo_byte_width = bwry2bpp ? BytesPerRow2bpp(img_w_)
                                          : BytesPerRow1bpp(img_w_);
    const bool mono1bpp = IsMono1bppImage(img_w_, img_h_, size_);
    const int expected_rows = (bwry2bpp || mono1bpp)
        ? std::min<int>(img_h_, static_cast<int>(size_) / photo_byte_width)
        : 0;
    int start_y = (height - expected_rows) / 2;
    if (start_y < 0) start_y = 0;

    const int draw_w = std::min(width, img_w_);
    const int start_x = std::max(0, (width - draw_w) / 2);

    for (int row = 0; row < expected_rows && (start_y + row) < height; row++) {
        for (int tx = 0; tx < draw_w; ++tx) {
            const Color src_color = ReadPixelColor(data_, size_, img_w_,
                                                    bwry2bpp, tx, row);
            set_pixel(fb, width, start_x + tx, start_y + row, src_color);
        }
    }
}

bool ScreenshotRenderer::HandleInput(const ButtonEvent& event) {
    // BOOT long-press: clear the cached image and revert to placeholder.
    // Lets the user recover from a stale/pushed screenshot without the NAS.
    if (event.type == ButtonEvent::kBootLongPress) {
        ClearImage();
        return true;
    }
    return false;
}

}  // namespace rawdraw
