/**
 * @file lifebar_renderer.cc
 * @brief Life progress page renderer implementation
 *
 * Large circular gauge showing life percentage, age, days elapsed/remaining,
 * weekends remaining, and a motivational quote.
 * Default: birthdate 1990-01-01, 80-year lifespan.
 */

#include "lifebar_renderer.h"
#include "rawdraw/rawdraw.h"
#include "rawdraw/layout_utils.h"  // FIX: 使用 InkCenteredTextTopY 替代 line_height 居中
#include "rawdraw/components/progress_bar.h"
#include "rawdraw/theme.h"
#include <algorithm>
#include <cstdio>
#include <ctime>

// External font references
extern const lv_font_t SourceHanSansSC_Regular_slim;
extern const lv_font_t SourceHanSansSC_Medium_slim;
extern const lv_font_t font_zectrix_16_1;

namespace rawdraw {

// ============================================================
// Configurable defaults
// ============================================================

static constexpr int BIRTH_YEAR  = 1990;
static constexpr int BIRTH_MONTH = 1;
static constexpr int BIRTH_DAY   = 1;
static constexpr int EXPECTED_LIFESPAN_YEARS = 80;

// Motivational quotes (rotated by index)
static const char* kQuotes[] = {
    "时间是最公平的，\n每人每天都只有24小时",
    "余生很长，何必慌张；\n余生很短，何必平凡",
    "把每一天当成\n生命中最后一天来过",
    "种一棵树最好的时间\n是十年前，其次是现在",
    "人生没有白走的路，\n每一步都算数",
};
static constexpr int kNumQuotes = sizeof(kQuotes) / sizeof(kQuotes[0]);

// ============================================================
// Lifecycle
// ============================================================

LifeBarRenderer::LifeBarRenderer()
    : title_font_(&SourceHanSansSC_Medium_slim)
    , body_font_(&SourceHanSansSC_Regular_slim)
    , small_font_(&SourceHanSansSC_Regular_slim)
    , age_years_(0)
    , age_months_(0)
    , days_elapsed_(0)
    , days_remaining_(0)
    , weekends_remaining_(0)
    , life_pct_(0)
    , visible_(true) {
}

LifeBarRenderer::~LifeBarRenderer() {}

// ============================================================
// Data calculation
// ============================================================

static bool is_leap(int y) {
    return (y % 4 == 0 && y % 100 != 0) || (y % 400 == 0);
}

static int days_in_month(int y, int m) {
    static const int d[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    if (m == 1 && is_leap(y)) return 29;
    return d[m];
}

void LifeBarRenderer::UpdateStats() {
    time_t now = time(nullptr);
    struct tm tm_now;
    localtime_r(&now, &tm_now);

    int cur_year  = tm_now.tm_year + 1900;
    int cur_month = tm_now.tm_mon + 1;   // 1-based
    int cur_day   = tm_now.tm_mday;

    // Days elapsed since birth
    int total_days = 0;

    // Full years
    for (int y = BIRTH_YEAR; y < cur_year; y++) {
        total_days += is_leap(y) ? 366 : 365;
    }

    // Full months of current year
    for (int m = 0; m < cur_month - 1; m++) {
        total_days += days_in_month(cur_year, m);
    }

    // Days of current month (minus birth offset for first year)
    if (cur_year == BIRTH_YEAR) {
        total_days += cur_day - BIRTH_DAY;
    } else {
        total_days += cur_day;
    }

    if (total_days < 0) total_days = 0;

    // Total lifespan in days
    int lifespan_days = 0;
    for (int y = BIRTH_YEAR; y < BIRTH_YEAR + EXPECTED_LIFESPAN_YEARS; y++) {
        lifespan_days += is_leap(y) ? 366 : 365;
    }

    // Age
    age_years_ = cur_year - BIRTH_YEAR;
    age_months_ = cur_month - BIRTH_MONTH;
    if (age_months_ < 0) {
        age_years_--;
        age_months_ += 12;
    }
    if (cur_day < BIRTH_DAY) {
        age_months_--;
        if (age_months_ < 0) {
            age_years_--;
            age_months_ += 12;
        }
    }

    days_elapsed_ = total_days;
    days_remaining_ = lifespan_days - total_days;
    if (days_remaining_ < 0) days_remaining_ = 0;

    // Weekends remaining (roughly 2/7 of remaining days)
    weekends_remaining_ = (days_remaining_ * 2) / 7;

    // Life percentage
    if (lifespan_days > 0) {
        life_pct_ = (total_days * 100) / lifespan_days;
    }
    if (life_pct_ > 100) life_pct_ = 100;
}

// ============================================================
// Init
// ============================================================

void LifeBarRenderer::Init(int width, int height) {
    width_ = width;
    height_ = height;
    needs_full_refresh_ = true;
    UpdateStats();
}

// ============================================================
// Render
// ============================================================

void LifeBarRenderer::Render(uint8_t* fb, int width, int height) {
    if (!fb) return;
    const auto& theme = ThemeManager::Get();
    const Color text = theme.ColorFor(ThemeToken::TextPrimary);
    const Color secondary = theme.ColorFor(ThemeToken::TextSecondary);

    if (!visible_) {
        // Show hidden placeholder
        const char* msg = "人生进度页已隐藏";
        int msg_w = MeasureTextWidth(msg, small_font_);
        int msg_x = (width - msg_w) / 2;
        msg_x = (msg_x + 7) & ~7;
        int msg_y = InkCenteredTextTopY(small_font_, msg, height / 2, 0);
        DrawText(fb, width, msg_x, msg_y, msg, small_font_, text);
        needs_full_refresh_ = false;
        return;
    }

    UpdateStats();

    // Fullscreen layout (page is chrome-free now). Use the whole 400x300 panel.
    // Layout from top:
    //   y=8   title "人生进度" (small, top-left feel)
    //   y=30  big circular gauge (r=95) centred horizontally, with % inside
    //   below gauge: 2 lines of stats (age / days remaining)
    //   bottom: 1 short quote line
    int y = 8;

    // Title (left-aligned, compact, secondary colour so it doesn't shout)
    {
        const char* title = "人生进度";
        DrawText(fb, width, 16, y, title, small_font_, secondary);
    }

    // === Circular gauge — the hero element ===
    RenderGauge(fb, width, y + small_font_->line_height + 4, height);

    needs_full_refresh_ = false;
}

void LifeBarRenderer::RenderHeader(uint8_t* fb, int width, int y) const {
    const auto& theme = ThemeManager::Get();
    const Color text = theme.ColorFor(ThemeToken::TextPrimary);
    const Color secondary = theme.ColorFor(ThemeToken::TextSecondary);
    // Title
    const char* title = "人生进度";
    int title_w = MeasureTextWidth(title, title_font_);
    int title_x = (width - title_w) / 2;
    title_x = (title_x + 7) & ~7;
    DrawText(fb, width, title_x, y, title, title_font_, text);

    // Subtitle
    const char* sub = "每一天都值得珍惜";
    int sub_w = MeasureTextWidth(sub, small_font_);
    int sub_x = (width - sub_w) / 2;
    sub_x = (sub_x + 7) & ~7;
    int sub_y = y + title_font_->line_height + Style::kSpacingXXS;
    DrawText(fb, width, sub_x, sub_y, sub, small_font_, secondary);
}

void LifeBarRenderer::RenderGauge(uint8_t* fb, int width, int y_start, int screen_height) const {
    const auto& theme = ThemeManager::Get();
    const Color text = theme.ColorFor(ThemeToken::TextPrimary);
    const Color secondary = theme.ColorFor(ThemeToken::TextSecondary);
    const Color accent = theme.ColorFor(ThemeToken::Accent);      // 红 = 已过
    const Color success = theme.ColorFor(ThemeToken::SuccessLike); // 黄 = 剩余背景环

    // Gauge geometry — enlarged now that the page is fullscreen.
    const int gauge_r = 92;
    const int gauge_thickness = 10;
    const int cx = width / 2;
    // Vertically centre the gauge in the area below the title.
    const int cy = y_start + gauge_r + 4;

    const Point center = {cx, cy};
    // Dual-colour ring: remaining = yellow background, elapsed = red fill.
    DrawCircularProgress(fb, width, center, gauge_r, gauge_thickness,
                         life_pct_, success, accent);

    // === Inside the ring: big percentage ===
    char pct_buf[16];
    snprintf(pct_buf, sizeof(pct_buf), "%d%%", life_pct_);
    int pct_w = MeasureTextWidth(pct_buf, title_font_);
    int pct_x = cx - pct_w / 2;
    pct_x = (pct_x + 7) & ~7;
    int pct_y = InkCenteredTextTopY(title_font_, pct_buf, cy - 6, 0);
    DrawText(fb, width, pct_x, pct_y, pct_buf, title_font_, accent);
    // Small "已过" label under the number
    const char* label = "已过";
    int lw = MeasureTextWidth(label, small_font_);
    DrawText(fb, width, (cx - lw / 2 + 7) & ~7,
             pct_y + title_font_->line_height, label, small_font_, secondary);

    // === Stats below gauge: 2 compact lines ===
    const int bottom_limit = screen_height - 8;
    int stats_y = cy + gauge_r + gauge_thickness / 2 + 12;
    char buf[64];

    // Line 1: age + days elapsed (primary text)
    if (stats_y + small_font_->line_height <= bottom_limit) {
        snprintf(buf, sizeof(buf), "%d岁%d月 · 已度过 %d 天",
                 age_years_, age_months_, days_elapsed_);
        int w = MeasureTextWidth(buf, small_font_);
        DrawText(fb, width, ((width - w) / 2 + 7) & ~7, stats_y, buf, small_font_, text);
        stats_y += small_font_->line_height + 2;
    }

    // Line 2: days + weekends remaining (secondary)
    if (stats_y + small_font_->line_height <= bottom_limit) {
        snprintf(buf, sizeof(buf), "余生约 %d 天 · 还有 %d 个周末",
                 days_remaining_, weekends_remaining_);
        int w = MeasureTextWidth(buf, small_font_);
        DrawText(fb, width, ((width - w) / 2 + 7) & ~7, stats_y, buf, small_font_, secondary);
        stats_y += small_font_->line_height + 6;
    }

    // Bottom: one short quote line (rotated daily) — only first line to stay clean
    if (stats_y + small_font_->line_height <= bottom_limit) {
        time_t now = time(nullptr);
        struct tm tm_buf;
        localtime_r(&now, &tm_buf);
        const char* quote = kQuotes[(tm_buf.tm_yday) % kNumQuotes];
        // Take only up to the first newline
        char line[64] = {};
        int i = 0;
        const char* p = quote;
        while (*p && *p != '\n' && i < 63) line[i++] = *p++;
        int w = MeasureTextWidth(line, small_font_);
        DrawText(fb, width, ((width - w) / 2 + 7) & ~7, stats_y, line, small_font_, secondary);
    }
}

void LifeBarRenderer::RenderQuote(uint8_t* fb, int width, int y) const {
    if (y + small_font_->line_height > height_ - Style::kSpacingSM) {
        return;  // Not enough space
    }

    // Pick quote by day (rotates daily)
    time_t now = time(nullptr);
    struct tm tm_buf;
    localtime_r(&now, &tm_buf);
    int idx = (tm_buf.tm_yday) % kNumQuotes;
    const char* quote = kQuotes[idx];

    // Draw quote lines
    char line[64];
    int line_idx = 0;
    int max_lines = 2;
    const char* p = quote;

    while (*p && line_idx < max_lines) {
        int i = 0;
        while (*p && *p != '\n' && i < 63) {
            line[i++] = *p++;
        }
        line[i] = '\0';
        if (*p == '\n') p++;

        int w = MeasureTextWidth(line, small_font_);
        int x = (width - w) / 2;
        x = (x + 7) & ~7;
        DrawText(fb, width, x, y + line_idx * (small_font_->line_height + Style::kSpacingXS),
                 line, small_font_, ThemeManager::Get().ColorFor(ThemeToken::TextSecondary));
        line_idx++;
    }
}

// ============================================================
// Input handling
// ============================================================

bool LifeBarRenderer::HandleInput(const ButtonEvent& event) {
    // No interactive navigation needed yet — page is informational
    (void)event;
    return false;
}

}  // namespace rawdraw
