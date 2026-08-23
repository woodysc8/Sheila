from zipfile import ZipFile
from pathlib import Path

zip_path = Path('C:/Users/Sam/Desktop/ruflo-main.zip')
output_path = Path('C:/Users/Sam/Desktop/jarvis-desktop/ruflo_main_listing.txt')

if not zip_path.exists():
    raise FileNotFoundError(f"{zip_path} not found")

with ZipFile(zip_path, 'r') as z:
    names = z.namelist()
output_path.write_text('\n'.join(names), encoding='utf-8')
print(f"Wrote {len(names)} entries to {output_path}")
