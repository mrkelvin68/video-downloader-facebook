# Video Downloader Facebook

Ứng dụng desktop Windows viết bằng Python và PySide6 để quản lý dự án video, kiểm tra danh sách Facebook từ CSV, tải từng video Facebook và lưu trạng thái trong SQLite.

## Chức năng hiện có

- Tạo và chỉnh sửa dự án với tên, prefix, thư mục video và ghi chú.
- Từ chối tên dự án/thư mục trùng, đường dẫn không hợp lệ và thư mục có sẵn không thuộc dự án.
- Lưu dữ liệu dự án và video trong SQLite; dữ liệu vẫn có sau khi đóng và chạy lại ứng dụng.
- Đọc trước CSV, kiểm tra toàn bộ dữ liệu, rồi mới cho phép import.
- Hỗ trợ dấu phẩy hoặc chấm phẩy, UTF-8/UTF-8 BOM, CSV Excel và trường có dấu ngoặc kép.
- Hiển thị video trong `QTableView`; dữ liệu nhiều dòng không tạo widget riêng cho từng video.
- Xuất CSV từ dữ liệu SQLite của dự án đang mở.
- Tải đơn lẻ từng video Facebook (Phase 3.1): tải video chất lượng cao nhất bằng yt-dlp, gom file MP4 và thumbnail vào thư mục riêng theo prefix, kiểm tra hợp lệ bằng FFprobe và cập nhật SQLite.
- Hàng đợi tuần tự Phase 3.2 với **Chạy ngay**, **Thêm vào hàng đợi**, ưu tiên job, tạm dừng sau video hiện tại, tiếp tục và retry. Tiến độ/tốc độ lấy từ yt-dlp; job lưu trong SQLite và được phục hồi an toàn sau restart.

## Chức năng chưa triển khai

- Tải song song (concurrency luôn bằng 1), scheduler hoặc Facebook Publisher.
- Dashboard, live logs và cài đặt.
- Xóa dự án hoặc di chuyển dữ liệu của dự án.

## Yêu cầu

- Windows 10/11.
- Python 3.11 trở lên.
- PySide6, yt-dlp, static-ffmpeg (cài tự động từ `requirements.txt`).

## Cài đặt và phát hành Windows

Nhà phát triển cài môi trường bằng:

```text
py install.py
py run.py
```

Tạo bản phát hành onedir (mặc định):

```text
py build.py
```

Tùy chọn onefile: `py build.py --onefile`. Bản phát hành nằm trong `dist/`, cùng ZIP `VideoDownloader-Windows-x64.zip`. Người dùng cuối chỉ cần giải nén ZIP và chạy `VideoDownloader.exe`; không cần Python, pip hoặc FFmpeg.

## Cài đặt và khởi chạy nhanh
Yêu cầu Windows 10/11 và Python 3.11 trở lên có trong `PATH` (Python Launcher `py` được khuyến nghị). Tại thư mục repository, cài lần đầu bằng:

```powershell
py install.py
```

Installer tạo hoặc tái sử dụng `.venv`, cài các gói từ `requirements.txt` vào môi trường đó và kiểm tra PySide6. Chạy lại lệnh an toàn; installer không thay đổi Python global và không xóa môi trường hiện có.

Mở ứng dụng bằng:

```powershell
py run.py
```

Hoặc nhấp đúp `start.bat`. Không cần tự activate môi trường hoặc cài thư viện bằng pip. `run.py` chỉ sử dụng Python trong `.venv`, không cài dependency khi khởi chạy. Ứng dụng chỉ cho phép một cửa sổ chạy cùng lúc.

## Tạo dự án

1. Chọn **Tạo dự án mới**.
2. Nhập tên dự án và prefix, ví dụ `ep1-6`.
3. Chọn một đường dẫn tuyệt đối cho thư mục lưu video. Thư mục đích phải chưa tồn tại để tránh ghi đè.
4. Lưu dự án. Ứng dụng tự tạo thư mục đích và lưu thông tin trong SQLite.

Mã video được tạo theo `{prefix}-{stt}`, ví dụ `ep1-6-1`. Khi dự án đã có video, ứng dụng từ chối đổi prefix hoặc thư mục output để tránh làm sai mã và liên kết dữ liệu.

## Định dạng CSV

CSV phải có đủ ba cột `stt,ten_video,url`. Hai alias cũ `sequence` và `title` được hỗ trợ tương ứng với `stt` và `ten_video`.

```csv
stt,ten_video,url
1,Video mở đầu,https://www.facebook.com/watch/?v=123456
2,"Video, có dấu phẩy",https://www.facebook.com/watch/?v=789012
```

`stt` phải là số nguyên dương và không trùng; tên video không được rỗng; URL phải thuộc Facebook. Dòng thiếu/thừa cột hoặc bất kỳ dòng sai nào khiến toàn bộ CSV không thể import. Có CSV mẫu tại [samples/videos.csv](./samples/videos.csv).

## Import và export CSV

1. Mở dự án, chọn hoặc kéo thả tệp CSV.
2. Kiểm tra bảng preview và các lỗi được báo theo số dòng. Preview chưa được lưu vào dự án.
3. Chọn **Xác nhận import** để lưu. Nếu dự án đã có danh sách, ứng dụng yêu cầu xác nhận trước khi thay thế.
4. Nếu có video đã tải/đang tải hoặc có đường dẫn file liên kết, thao tác thay thế bị chặn. Việc import diễn ra trong một transaction; lỗi sẽ rollback dữ liệu cũ.
5. **Xuất CSV** lấy dữ liệu đã lưu trong SQLite của dự án hiện tại, theo STT tăng dần và có UTF-8 BOM. Preview chưa xác nhận không được export.

## Download Queue

1. Trong trang dự án, chọn một hoặc nhiều video bằng checkbox. Có thể dùng **Chọn tất cả** hoặc **Bỏ chọn tất cả**.
2. **Chạy ngay** đưa video đã chọn lên đầu hàng đợi và bắt đầu queue. Video đang chạy không bị ngắt.
3. **Thêm vào hàng đợi** chỉ lưu job, không tự bắt đầu tải.
4. Mở **Hàng đợi tải video** ở sidebar hoặc **Xem hàng đợi** để theo dõi trạng thái, tiến độ thực, tốc độ, dung lượng, ETA ước tính và lỗi.
5. Queue chạy từng video một. **Tạm dừng sau video hiện tại** không ngắt file đang tải; **Tiếp tục** bắt đầu các job còn chờ.
6. Job gián đoạn khi ứng dụng đóng bất thường được đánh dấu để người dùng chủ động Retry/tiếp tục. Ứng dụng không tự tải lại job đó.
7. Xóa job đang chờ chỉ bỏ job khỏi queue, không xóa file video. Job đang tải không thể bị xóa bằng thao tác này.

## Cấu trúc source code

```text
app/
├── core/                 # Validator, exception, khóa một instance
├── database/             # Kết nối SQLite và migration
├── models/               # Project, Video
├── services/             # Project, CSV, video services
└── ui/
    ├── dialogs/
    ├── pages/
    ├── widgets/
    ├── main_window.py
    └── styles.qss
main.py                   # Entry point
install.py                # Tạo .venv và cài dependencies
run.py                    # Khởi chạy bằng Python trong .venv
start.bat                 # Launcher Windows không hiện console khi chạy bình thường
```

CSV parser nằm trong service thuần Python và không import PySide6. UI gọi services, còn câu lệnh SQL và transaction nằm trong database layer.

## Vị trí SQLite database

Mặc định database ở:

```text
%USERPROFILE%\.video-downloader-facebook\projects.sqlite3
```

Không xóa hoặc sửa file database khi ứng dụng đang chạy. Repository không chứa database người dùng. Migration legacy video chạy trong transaction; schema `projects` hiện tại được giữ nguyên. Nếu schema cũ không nhận diện được, migration dừng và giữ bảng dữ liệu cũ.

## Chạy test

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest -v
```

Test dùng SQLite thực trên file tạm, bao gồm parser, transaction rollback, migration, cô lập dự án, import 1.000 dòng, UI preview/cancel/restart/export, model bảng và cài đặt/khởi chạy trong thư mục tạm. Test launcher không cài gói ra Python global.

## Lỗi thường gặp

- **Không có quyền ghi thư mục:** chọn thư mục cha mà tài khoản Windows có quyền tạo thư mục con.
- **Không tìm thấy Python:** cài Python 3.11 trở lên và bật tùy chọn thêm Python vào `PATH`; mở lại PowerShell rồi kiểm tra `py --version`.
- **Python sai phiên bản:** chạy installer bằng một Python 3.11 trở lên, ví dụ `py -3.11 install.py`. Installer không tự thay đổi interpreter hoặc xóa `.venv` hiện tại.
- **Thiếu PySide6:** chạy lại `py install.py`; khi lỗi vẫn còn, xem thông báo pip để kiểm tra mạng, proxy hoặc quyền ghi.
- **Tạo virtual environment thất bại:** kiểm tra quyền ghi repository, cài đặt Python đầy đủ (bao gồm `venv`/`ensurepip`), rồi thử lại. `.venv` hiện có sẽ không bị tự xóa.
- **Cài dependency thất bại:** kiểm tra kết nối tới package index và thông báo lỗi `pip`; dùng `py install.py` để thử lại. Gói chỉ được cài vào `.venv`.
- **Ứng dụng không khởi chạy:** chạy `py run.py` từ PowerShell để xem lỗi cụ thể; kiểm tra `.venv`, PySide6, quyền truy cập database và các migration được báo.
- **Nhấp đúp start.bat báo thiếu môi trường:** chạy `py install.py`, sau đó nhấp đúp lại. Nếu lỗi startup khác, dùng `py run.py` để đọc chi tiết.
- **Thư mục đã tồn tại:** chọn một đường dẫn mới; ứng dụng không tái sử dụng thư mục có thể chứa dữ liệu khác.
- **CSV thiếu cột hoặc sai dòng:** dùng đủ `stt,ten_video,url`, sửa dòng được báo và import lại toàn bộ file.
- **Không thể thay thế danh sách:** dự án có video đang tải/đã tải hoặc đang liên kết file; không xóa dữ liệu/file thủ công để vượt qua kiểm tra.
- **Không thể đổi prefix/output directory:** dự án đã có video; phiên bản này chưa hỗ trợ migration an toàn cho các liên kết đó.
- **Không mở được database:** đóng ứng dụng khác đang sử dụng database và kiểm tra quyền truy cập; dữ liệu không bị tự xóa khi khởi tạo/migration thất bại.
