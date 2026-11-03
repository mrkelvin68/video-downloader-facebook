# Video Downloader Facebook

Ứng dụng desktop Windows viết bằng Python và PySide6 để quản lý dự án video, kiểm tra danh sách Facebook từ CSV và lưu danh sách video theo từng dự án trong SQLite. Phiên bản hiện tại dừng ở Phase 2; ứng dụng chưa tải video.

## Chức năng hiện có

- Tạo và chỉnh sửa dự án với tên, prefix, thư mục video và ghi chú.
- Từ chối tên dự án/thư mục trùng, đường dẫn không hợp lệ và thư mục có sẵn không thuộc dự án.
- Lưu dữ liệu dự án và video trong SQLite; dữ liệu vẫn có sau khi đóng và chạy lại ứng dụng.
- Đọc trước CSV, kiểm tra toàn bộ dữ liệu, rồi mới cho phép import.
- Hỗ trợ dấu phẩy hoặc chấm phẩy, UTF-8/UTF-8 BOM, CSV Excel và trường có dấu ngoặc kép.
- Hiển thị video trong `QTableView`; dữ liệu nhiều dòng không tạo widget riêng cho từng video.
- Xuất CSV từ dữ liệu SQLite của dự án đang mở.

## Chức năng chưa triển khai

- Tải video Facebook, hàng đợi tải, tiến độ thực tế, retry và quản lý file video.
- Dashboard, live logs và cài đặt.
- Xóa dự án hoặc di chuyển dữ liệu của dự án.

Không có Download Engine trong phiên bản này.

## Yêu cầu

- Windows 10/11.
- Python 3.11 trở lên.
- PySide6 (cài tự động từ `requirements.txt`).

## Cài đặt

Mở PowerShell tại thư mục repository:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Chạy ứng dụng

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

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

## Cấu trúc source code

```text
app/
├── core/                 # Validator và exception nghiệp vụ
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
.\.venv\Scripts\Activate.ps1
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest -v
```

Test dùng SQLite thực trên file tạm, bao gồm parser, transaction rollback, migration, cô lập dự án, import 1.000 dòng, UI preview/cancel/restart/export và model bảng.

## Lỗi thường gặp

- **Không có quyền ghi thư mục:** chọn thư mục cha mà tài khoản Windows có quyền tạo thư mục con.
- **Thư mục đã tồn tại:** chọn một đường dẫn mới; ứng dụng không tái sử dụng thư mục có thể chứa dữ liệu khác.
- **CSV thiếu cột hoặc sai dòng:** dùng đủ `stt,ten_video,url`, sửa dòng được báo và import lại toàn bộ file.
- **Không thể thay thế danh sách:** dự án có video đang tải/đã tải hoặc đang liên kết file; không xóa dữ liệu/file thủ công để vượt qua kiểm tra.
- **Không thể đổi prefix/output directory:** dự án đã có video; phiên bản này chưa hỗ trợ migration an toàn cho các liên kết đó.
- **Không mở được database:** đóng ứng dụng khác đang sử dụng database và kiểm tra quyền truy cập; dữ liệu không bị tự xóa khi khởi tạo/migration thất bại.
