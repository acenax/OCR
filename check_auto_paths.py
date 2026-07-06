from app.auto_paths import diagnostic
try:
    from app.config import load_config
    cfg = load_config()
    print(diagnostic(cfg.get("tesseract_path", ""), cfg.get("poppler_path", ""), [cfg.get("root_folder", "")]))
except Exception as e:
    print("Auto path diagnostic failed:", e)
    print(diagnostic())
