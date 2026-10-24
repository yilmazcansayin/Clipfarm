"""chrome/isaretle.js dosyasından Chrome'a eklenecek tek satırlık yer imi adresini üretir."""

import re
from pathlib import Path

here = Path(__file__).parent
src = (here / "isaretle.js").read_text(encoding="utf-8")
src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
code = " ".join(line.strip() for line in src.splitlines() if line.strip())
out = "javascript:" + code.replace("%", "%25")
(here / "bookmarklet.txt").write_text(out + "\n", encoding="utf-8")
print(out)
