from dotenv import load_dotenv
load_dotenv()

from web import create_app

app = create_app()

if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        use_reloader=False  # Must be False to avoid APScheduler running twice
    )
