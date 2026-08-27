/**
 * @file ebook_renderer.h
 * @brief Ebook list page and TXT reader renderer for rawdraw mode
 */

#ifndef RAWDRAW_EBOOK_RENDERER_H
#define RAWDRAW_EBOOK_RENDERER_H

#include "page_renderer.h"
#include "rawdraw/style.h"
#include <vector>
#include <string>

namespace rawdraw {

class EbookRenderer : public PageRenderer {
public:
    EbookRenderer();
    ~EbookRenderer() override;

    void Init(int width, int height) override;
    void Render(uint8_t* fb, int width, int height) override;
    bool HandleInput(const ButtonEvent& event) override;

    // File list mode
    void SetFileList(const std::vector<std::string>& files);
    int GetSelectedIndex() const { return selected_index_; }
    std::string GetSelectedFile() const;

    // Reader mode
    void OpenFile(const std::string& filename, const std::string& content);
    void CloseReader();
    bool IsReaderMode() const { return reader_mode_; }
    bool IsPortraitReader() const { return reader_mode_ && portrait_reader_; }
    const std::string& GetReaderFilename() const { return reader_filename_; }
    int GetCurrentPage() const { return current_page_; }
    int GetTotalPages() const { return total_pages_; }

private:
    void RenderFileList(uint8_t* fb, int width, int height);
    void RenderReader(uint8_t* fb, int width, int height);
    void RenderReaderPage(uint8_t* fb, int width, int height, int content_y, int content_h);
    void RenderReaderPortrait(uint8_t* fb, int width, int height);

    // Line-based pagination: the whole file is wrapped into display lines
    // once per orientation, and a page is a group of max_lines_ consecutive
    // lines. The old byte-count pagination (450/620 bytes per page) silently
    // dropped text: lines that wrapped beyond max_lines neither showed on
    // the page nor appeared on the next one, and the byte boundary could cut
    // a multi-byte UTF-8 character in half.
    struct ReaderLine {
        std::string text;   // display line (no trailing '\n')
        int offset;         // byte offset of the line start in reader_content_
    };
    void BuildReaderLines();
    void ReaderContentArea(int& content_y, int& content_h) const;
    int ReaderWrapWidth(int width) const;
    void SetPortraitReader(bool portrait);

    std::vector<std::string> files_;
    int selected_index_ = 0;

    // Reader state
    bool reader_mode_ = false;
    bool portrait_reader_ = false;
    std::string reader_filename_;
    std::string reader_content_;
    std::vector<ReaderLine> reader_lines_;
    int line_box_h_ = 24;  // per-line vertical step box (ink height + padding)
    int max_lines_ = 1;    // lines per page for the current orientation
    int current_page_ = 0;
    int total_pages_ = 0;

    const lv_font_t* font_ = nullptr;
    const lv_font_t* title_font_ = nullptr;
};

}  // namespace rawdraw

#endif  // RAWDRAW_EBOOK_RENDERER_H
