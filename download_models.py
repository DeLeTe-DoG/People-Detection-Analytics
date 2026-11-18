"""Скачивает модели распознавания лиц в папку models/.

    python download_models.py          скачать недостающее
    python download_models.py --check  только проверить, что файлы на месте

Если скачать не получилось (нет интернета, прокси, блокировка GitHub) — скачайте два
файла в браузере (прямые ссылки печатаются ниже) и положите в папку models/.
Без этих файлов программа работает, но без опознавания сотрудников по лицу.
"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.faces import DET_MODEL, MODELS_DIR, REC_MODEL_INT8  # noqa: E402

REPO = "opencv/opencv_zoo"
FILES = {
    # имя файла: (папка в репозитории, минимальный правдоподобный размер в байтах)
    DET_MODEL: ("face_detection_yunet", 150_000),          # ~230 КБ
    REC_MODEL_INT8: ("face_recognition_sface", 8_000_000),  # ~10 МБ
}


def urls(name):
    folder = FILES[name][0]
    return [
        f"https://github.com/{REPO}/raw/main/models/{folder}/{name}",
        f"https://media.githubusercontent.com/media/{REPO}/main/models/{folder}/{name}",
    ]


def ok_file(name):
    path = os.path.join(MODELS_DIR, name)
    return os.path.exists(path) and os.path.getsize(path) >= FILES[name][1]


def fetch(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (OfficeAnalytics)"})
    tmp = path + ".part"
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, path)


def main():
    os.makedirs(MODELS_DIR, exist_ok=True)
    missing = [n for n in FILES if not ok_file(n)]
    if "--check" in sys.argv or not missing:
        for n in FILES:
            print(("OK      " if ok_file(n) else "НЕТ     ") + n)
        sys.exit(0 if not missing else 1)

    for name in missing:
        path = os.path.join(MODELS_DIR, name)
        done = False
        for url in urls(name):
            print(f"скачиваю {name}\n  из {url}")
            try:
                fetch(url, path)
                if not ok_file(name):                       # пришла заглушка Git LFS или страница ошибки
                    os.remove(path)
                    raise OSError("получен файл неверного размера")
                print("  готово")
                done = True
                break
            except Exception as e:  # noqa: BLE001
                print(f"  не вышло: {type(e).__name__}: {e}")
                for leftover in (path, path + ".part"):
                    if os.path.exists(leftover):
                        os.remove(leftover)
        if not done:
            print(f"\nНЕ УДАЛОСЬ СКАЧАТЬ {name}")

    still = [n for n in FILES if not ok_file(n)]
    if still:
        print("\n=== Скачайте вручную (в браузере, можно на другом компьютере) ===")
        for n in still:
            print(f"  {urls(n)[0]}")
        print(f"и положите файлы в папку:\n  {MODELS_DIR}\n"
              "Имена файлов менять не нужно. Потом проверьте: python download_models.py --check\n"
              "Пока файлов нет, программа работает без распознавания лиц.")
        sys.exit(1)


if __name__ == "__main__":
    main()
