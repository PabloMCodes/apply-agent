"""Start the local API: python main.py."""

from src.api.app import create_app

app = create_app(start_workers=True)


if __name__ == '__main__':
    import os
    import uvicorn
    from src.applications.native import desktop_available

    os.environ.setdefault('BROWSER_MODE', 'native' if desktop_available() else 'stream')

    uvicorn.run(app, host='127.0.0.1', port=8000)
