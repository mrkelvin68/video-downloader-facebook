from __future__ import annotations

import sys

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--yt-dlp":
    from yt_dlp import main as yt_dlp_main

    sys.exit(yt_dlp_main(sys.argv[2:]))

from app import main

if __name__ == "__main__":
    sys.exit(main())
