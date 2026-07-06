import py_compile
for f in ['app/ocr_template_v2.py', 'app/ocr.py']:
    try:
        py_compile.compile(f, doraise=True)
        print(f'{f}: OK')
    except Exception as e:
        print(f'{f}: ERROR {e}')
        raise
