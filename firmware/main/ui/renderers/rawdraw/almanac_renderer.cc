/**
 * @file almanac_renderer.cc
 * @brief Almanac page renderer - displays lunar date, solar terms, and almanac info
 *
 * Uses Calendar::ToLunarDate() for lunar calendar conversion (2000-2050).
 * Shows: today's Gregorian date, lunar date, solar term, weekday, and
 * traditional almanac info (yiji - auspicious/inauspicious activities).
 */

#include "almanac_renderer.h"
#include "rawdraw/rawdraw.h"
#include "rawdraw/style.h"
#include "rawdraw/layout_utils.h"  // FIX: 使用 InkCenteredTextTopYInBox 替代 line_height 居中
#include "rawdraw/components/calendar.h"
#include "rawdraw/theme.h"
#include <cstring>
#include <ctime>
#include <cstdio>

// External font references
extern const lv_font_t SourceHanSansSC_Regular_slim;
extern const lv_font_t SourceHanSansSC_Medium_slim;
extern const lv_font_t SourceHanSansSC_Big48_slim;  // 48px 大字号(方案C:固件原生清晰渲染)
extern const lv_font_t weather_icons_48;

// Weekday characters (matches calendar.cc)
static const char* kWeekdayFull[] = {"周日", "周一", "周二", "周三", "周四", "周五", "周六"};

// Lunar month/day names (same as calendar.cc)
static const char* kLunarMonths[] = {
    "正月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "十一月", "腊月"
};
static const char* kLunarDays[] = {
    "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十"
};

// Tian Gan / Di Zhi (used via Calendar::GetLunarYearName)
// static const char* kTianGan[] = ...;  // Not used directly, Calendar handles it
// static const char* kDiZhi[] = ...;

// Solar terms (same as calendar.cc)
struct SolarTermEntry {
    int month;
    int day;
    const char* name;
};
static const SolarTermEntry kSolarTerms[] = {
    { 1,  5, "小寒" }, { 1, 20, "大寒" },
    { 2,  4, "立春" }, { 2, 19, "雨水" },
    { 3,  5, "惊蛰" }, { 3, 20, "春分" },
    { 4,  4, "清明" }, { 4, 20, "谷雨" },
    { 5,  5, "立夏" }, { 5, 21, "小满" },
    { 6,  5, "芒种" }, { 6, 21, "夏至" },
    { 7,  7, "小暑" }, { 7, 23, "大暑" },
    { 8,  7, "立秋" }, { 8, 23, "处暑" },
    { 9,  7, "白露" }, { 9, 23, "秋分" },
    {10,  8, "寒露" }, {10, 23, "霜降" },
    {11,  7, "立冬" }, {11, 22, "小雪" },
    {12,  7, "大雪" }, {12, 22, "冬至" },
};

static const char* GetSolarTerm(int month, int day) {
    for (size_t i = 0; i < sizeof(kSolarTerms) / sizeof(kSolarTerms[0]); i++) {
        if (kSolarTerms[i].month == month && kSolarTerms[i].day == day) {
            return kSolarTerms[i].name;
        }
    }
    return nullptr;
}

// Simplified yiji (宜忌) based on lunar day patterns
// This is a traditional approximation, not a full almanac calculation
static const char* kYiTable[][4] = {
    {"祭祀", "祈福", "出行", "动土"},
    {"嫁娶", "纳采", "订盟", "出行"},
    {"开市", "交易", "立券", "纳财"},
    {"破土", "启钻", "安葬", "修坟"},
    {"修造", "动土", "起基", "定磉"},
    {"安床", "开市", "交易", "立券"},
    {"祭祀", "沐浴", "扫舍", "修造"},
    {"祈福", "求嗣", "出行", "解除"},
    {"嫁娶", "祭祀", "祈福", "出行"},
    {"开市", "立券", "交易", "纳财"},
};
static const char* kJiTable[][3] = {
    {"破土", "安葬", "启钻"},
    {"开仓", "出货财", "纳粟"},
    {"词讼", "争执", "诽谤"},
    {"嫁娶", "出行", "祈福"},
    {"安床", "移徙", "入宅"},
    {"祭祀", "修造", "动土"},
    {"开市", "纳财", "交易"},
    {"出行", "解除", "拆卸"},
    {"破土", "启钻", "安葬"},
    {"纳采", "订盟", "嫁娶"},
};

// ============================================================
// Local almanac computations (no network needed)
// All simplified traditional approximations.
// ============================================================

// 12 zodiac animals. 丙午年: 午=马. Earthly branch index = (year - 4) % 12.
static const char* kZodiac[] = {"鼠","牛","虎","兔","龙","蛇","马","羊","猴","鸡","狗","猪"};
static const char* GetZodiac(int year) {
    return kZodiac[((year - 4) % 12 + 12) % 12];
}

// Western zodiac by month/day. Clean lookup table.
static const char* GetConstellation(int month, int day) {
    // start day of the "second" sign for each month (1=Jan).
    // e.g. Jan: day<20 -> 摩羯, day>=20 -> 水瓶
    static const int kStart[13] = {0, 20, 19, 21, 20, 21, 22, 23, 23, 23, 24, 23, 22};
    static const char* kFirst[13] = {nullptr,"摩羯","水瓶","双鱼","白羊","金牛","双子","巨蟹","狮子","处女","天秤","天蝎","射手"};
    static const char* kSecond[13] = {nullptr,"水瓶","双鱼","白羊","金牛","双子","巨蟹","狮子","处女","天秤","天蝎","射手","摩羯"};
    if (month < 1 || month > 12) return "";
    return day < kStart[month] ? kFirst[month] : kSecond[month];
}

// Chong (冲) + Sha (煞) from earthly branch of day.
// Day branch index derived from a reference epoch is complex; use day-of-year as
// a stable pseudo-index so the value still rotates daily.
static const char* kChongAnimals[] = {"马","羊","猴","鸡","狗","猪","鼠","牛","虎","兔","龙","蛇"};
static const char* kSha[] = {"南","东","北","西","南","东","北","西","南","东","北","西"};
static void GetChongSha(int year, int month, int day, char* out_chong, size_t chong_sz,
                        char* out_sha, size_t sha_sz) {
    // day-of-year as pseudo branch index
    static const int dim[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    int doy = day;
    for (int m = 1; m < month; m++) {
        doy += (m==2 && ((year%4==0&&year%100!=0)||(year%400==0))) ? 29 : dim[m-1];
    }
    int idx = ((doy - 1) % 12 + 12) % 12;
    // 冲 = opposite of idx branch's animal
    int opp = (idx + 6) % 12;
    snprintf(out_chong, chong_sz, "冲%s", kChongAnimals[opp]);
    snprintf(out_sha, sha_sz, "煞%s", kSha[idx]);
}

// Auspicious directions (喜神/财神/福神) — simplified, rotates by day index.
static const char* kDirections[] = {"正东","正南","正西","正北","东南","东北","西南","西北"};
static void GetAuspiciousDirections(int year, int month, int day,
                                    const char*& xishen, const char*& caishen, const char*& fushen) {
    static const int dim[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    int doy = day;
    for (int m = 1; m < month; m++) {
        doy += (m==2 && ((year%4==0&&year%100!=0)||(year%400==0))) ? 29 : dim[m-1];
    }
    xishen = kDirections[(doy) % 8];
    caishen = kDirections[(doy + 3) % 8];
    fushen = kDirections[(doy + 5) % 8];
}

namespace rawdraw {

AlmanacRenderer::AlmanacRenderer()
    : font_(&SourceHanSansSC_Regular_slim)
    , title_font_(&SourceHanSansSC_Medium_slim)
    , big_font_(&SourceHanSansSC_Big48_slim)
    , icon_font_(&weather_icons_48) {
}

AlmanacRenderer::~AlmanacRenderer() = default;

void AlmanacRenderer::Init(int width, int height) {
    width_ = width;
    height_ = height;
    needs_full_refresh_ = true;
    RefreshData();
}

void AlmanacRenderer::RefreshData() {
    time_t now = time(nullptr);
    localtime_r(&now, &tm_);

    year_ = tm_.tm_year + 1900;
    month_ = tm_.tm_mon + 1;
    day_ = tm_.tm_mday;
    weekday_ = tm_.tm_wday;  // 0=Sun

    // Lunar date via Calendar algorithm
    lunar_ = Calendar::ToLunarDate(year_, month_, day_);
    lunar_year_name_ = Calendar::GetLunarYearName(year_);

    // Solar term
    solar_term_ = GetSolarTerm(month_, day_);

    // Yiji (宜忌) - simplified based on lunar day
    // ToLunarDate returns {0,0,0} outside its table range (e.g. RTC empty and
    // SNTP not yet synced -> 1970); (0-1)%10 == -1 would index kYiTable out
    // of bounds, so degrade to empty entries instead.
    if (lunar_.lunar_day < 1 || lunar_.lunar_day > 30) {
        static const char* kEmptyYi[] = {"", ""};
        static const char* kEmptyJi[] = {"", ""};
        yi_ = kEmptyYi;
        ji_ = kEmptyJi;
    } else {
        int yi_idx = (lunar_.lunar_day - 1) % 10;
        int ji_idx = (lunar_.lunar_day) % 10;
        yi_ = kYiTable[yi_idx];
        ji_ = kJiTable[ji_idx];
    }
}

void AlmanacRenderer::Render(uint8_t* fb, int width, int height) {
    if (!fb) return;

    // 大数字焦点型布局(方案C:固件原生渲染,无锯齿)
    // 参考网页版 board/templates/almanac.html 的 grid 布局:
    //   顶部:日期条(公历 + 农历年/生肖) — 红色下划线分隔
    //   左下:黄色焦点块 + 48px 农历大字(视觉中心)
    //   右下:宜(黑框)/忌(红框)两张卡片,只留关键词
    const auto& theme = ThemeManager::Get();
    const Color text = theme.ColorFor(ThemeToken::TextPrimary);
    const Color accent = theme.ColorFor(ThemeToken::Accent);        // 红
    const Color success = theme.ColorFor(ThemeToken::SuccessLike);   // 黄
    const Color danger = theme.ColorFor(ThemeToken::Danger);         // 红(忌)

    // ===== 顶部日期条(全宽,y=0~70)=====
    {
        const int pad_x = 24;
        const int y = 18;
        // 左:丙午年 马
        char year_buf[24];
        snprintf(year_buf, sizeof(year_buf), "%s年 %s",
                 lunar_year_name_, GetZodiac(year_));
        DrawText(fb, width, pad_x, y, year_buf, title_font_, text);
        // 右:公历 + 农历
        char solar_buf[40];
        snprintf(solar_buf, sizeof(solar_buf), "%d.%d.%d 周%s",
                 year_, month_, day_, kWeekdayFull[weekday_]);
        int sw = MeasureTextWidth(solar_buf, title_font_);
        DrawText(fb, width, width - pad_x - sw, y, solar_buf, title_font_, text);
        // 右下小字:农历月 + 节气
        char lunar_buf[32];
        if (solar_term_) {
            snprintf(lunar_buf, sizeof(lunar_buf), "%s月 %s",
                     GetLunarMonthName(lunar_.lunar_month), solar_term_);
        } else {
            snprintf(lunar_buf, sizeof(lunar_buf), "%s月",
                     GetLunarMonthName(lunar_.lunar_month));
        }
        int lw = MeasureTextWidth(lunar_buf, font_);
        DrawText(fb, width, width - pad_x - lw, y + title_font_->line_height + 2,
                 lunar_buf, font_, text);
        // 红色下划线分隔
        DrawHLine(fb, width, 70, pad_x, width - pad_x, accent);
        DrawHLine(fb, width, 71, pad_x, width - pad_x, accent);
    }

    // ===== 左下:黄色焦点块 + 48px 农历大字(x=0~200, y=80~300)=====
    {
        const int block_x = 0;
        const int block_y = 80;
        const int block_w = 200;
        const int block_h = height - block_y;  // 220
        // 黄色填充块
        DrawRect(fb, width, {block_x, block_y, block_w, block_h}, success);
        // 农历月(24px,顶部)
        const char* month_str = GetLunarMonthName(lunar_.lunar_month);
        int mw = MeasureTextWidth(month_str, title_font_);
        DrawText(fb, width, block_x + (block_w - mw) / 2, block_y + 20,
                 month_str, title_font_, text);
        // 农历日(48px 大字,焦点)— 红色
        const char* day_str = GetLunarDayName(lunar_.lunar_day);
        int dw = MeasureTextWidth(day_str, big_font_);
        int day_y = block_y + 20 + title_font_->line_height + 16;
        DrawText(fb, width, block_x + (block_w - dw) / 2, day_y,
                 day_str, big_font_, accent);
        // 生肖(16px,底部小字点缀)
        const char* animal = GetZodiac(year_);
        int aw = MeasureTextWidth(animal, font_);
        DrawText(fb, width, block_x + (block_w - aw) / 2,
                 day_y + big_font_->line_height + 12,
                 animal, font_, text);
    }

    // ===== 右下:宜/忌卡片(x=210~400, y=80~300)=====
    {
        const int card_x = 210;
        const int card_w = width - card_x - 18;  // ~172
        const int card_pad = 12;
        const int gap = 10;
        const int card_h = (height - 80 - gap - 14) / 2;  // ~98 each
        // 宜卡片(黑框)
        const int yi_y = 80;
        DrawRectBorder(fb, width, {card_x, yi_y, card_w, card_h}, 3, BLACK);
        DrawText(fb, width, card_x + card_pad, yi_y + 10, "宜", big_font_, text);
        int label_w = MeasureTextWidth("宜", big_font_);
        // 宜条目(只显示前2个,16px)
        char yi_items[32];
        snprintf(yi_items, sizeof(yi_items), "%s %s", yi_[0], yi_[1]);
        DrawText(fb, width, card_x + card_pad, yi_y + card_h - font_->line_height - 12,
                 yi_items, font_, text);

        // 忌卡片(红框)
        const int ji_y = yi_y + card_h + gap;
        DrawRectBorder(fb, width, {card_x, ji_y, card_w, card_h}, 3, danger);
        DrawText(fb, width, card_x + card_pad, ji_y + 10, "忌", big_font_, danger);
        char ji_items[32];
        snprintf(ji_items, sizeof(ji_items), "%s %s", ji_[0], ji_[1]);
        DrawText(fb, width, card_x + card_pad, ji_y + card_h - font_->line_height - 12,
                 ji_items, font_, text);
    }

    needs_full_refresh_ = false;
}

void AlmanacRenderer::DrawTitleBar(uint8_t* fb, int width) {
    const auto& theme = ThemeManager::Get();
    const PaintStyle bar_style = theme.Style(ThemeToken::BackgroundSecondary);
    const Color text = theme.ColorFor(ThemeToken::TextPrimary);
    const Color border = theme.ColorFor(ThemeToken::Border);
    const int title_y_start = Style::kStatusBarHeight;
    const int title_bar_h = kTitleBarH;

    // Background
    DrawStyledRect(fb, width, {0, title_y_start, width, title_bar_h}, bar_style);

    // Top divider (2px)
    DrawHLine(fb, width, title_y_start, 0, width, border);
    DrawHLine(fb, width, title_y_start + 1, 0, width, border);

    // Bottom divider (2px)
    const int line_y = title_y_start + title_bar_h - 2;
    DrawHLine(fb, width, line_y, 0, width, border);
    DrawHLine(fb, width, line_y + 1, 0, width, border);

    // FIX: 改用 InkCenteredTextTopYInBox，避免 line_height 居中导致中文偏上
    // 参见 wiki/projects/notellm-baseline-alignment.md
    int title_text_y = InkCenteredTextTopYInBox(font_, "老黄历", title_y_start, title_bar_h, 1);
    DrawText(fb, width, Style::kSpacingLG, title_text_y, "老黄历", font_, text);
}

const char* AlmanacRenderer::GetLunarMonthName(int month) {
    if (month < 1 || month > 12) return "";
    return kLunarMonths[month - 1];
}

const char* AlmanacRenderer::GetLunarDayName(int day) {
    if (day < 1 || day > 30) return "";
    return kLunarDays[day - 1];
}

bool AlmanacRenderer::HandleInput(const ButtonEvent& event) {
    switch (event.type) {
        case ButtonEvent::kUpClick:
        case ButtonEvent::kDownClick:
            // Navigate months (UP=prev, DOWN=next)
            if (event.type == ButtonEvent::kUpClick) {
                month_--;
                if (month_ < 1) { month_ = 12; year_--; }
            } else {
                month_++;
                if (month_ > 12) { month_ = 1; year_++; }
            }
            // Clamp day to the navigated month's length (31 → Apr → 30),
            // then recompute the weekday for that date. Otherwise the
            // header keeps showing today's weekday and lunar conversion
            // receives an invalid day.
            {
                static const int kDim[] = {31,28,31,30,31,30,31,31,30,31,30,31};
                int maxd = kDim[month_ - 1];
                if (month_ == 2 && ((year_%4==0 && year_%100!=0) || year_%400==0))
                    maxd = 29;
                if (day_ > maxd) day_ = maxd;

                struct tm nav{};
                nav.tm_year = year_ - 1900;
                nav.tm_mon = month_ - 1;
                nav.tm_mday = day_;
                if (mktime(&nav) != (time_t)-1)
                    weekday_ = nav.tm_wday;
            }
            lunar_ = Calendar::ToLunarDate(year_, month_, day_);
            // Recompute every derived field, not just lunar_: the year name
            // crosses lunar years and the yi/ji tables are keyed by lunar day,
            // so leaving them stale showed the previously viewed month's data.
            lunar_year_name_ = Calendar::GetLunarYearName(year_);
            if (lunar_.lunar_day < 1 || lunar_.lunar_day > 30) {
                static const char* kEmptyYi[] = {"", ""};
                static const char* kEmptyJi[] = {"", ""};
                yi_ = kEmptyYi;
                ji_ = kEmptyJi;
            } else {
                yi_ = kYiTable[(lunar_.lunar_day - 1) % 10];
                ji_ = kJiTable[(lunar_.lunar_day) % 10];
            }
            solar_term_ = GetSolarTerm(month_, day_);
            needs_full_refresh_ = true;
            return true;

        case ButtonEvent::kBootLongPress:
            // Jump to today
            RefreshData();
            needs_full_refresh_ = true;
            return true;

        default:
            break;
    }
    return false;
}

}  // namespace rawdraw
