import py_compile
for f in ['app/ocr_image_filters.py','app/ui/layout_teacher.py','app/ocr_template_v2.py']:
    try:
        py_compile.compile(f, doraise=True)
        print(f'{f}: OK')
    except FileNotFoundError:
        print(f'{f}: SKIP')
    except Exception as e:
        print(f'{f}: ERROR {e}')
        raise
