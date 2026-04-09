import os
from flask import Flask
from .extensions import db, login_manager, bcrypt


def create_app():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__, template_folder='templates')

    # Config
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-2024')
    db_path = os.path.join(base_dir, 'verificacoes.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', f'sqlite:///{db_path}')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Extensions
    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Faça login para acessar o painel.'
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        return User.query.get(int(user_id))

    # Blueprints
    from .auth.routes import auth_bp
    from .dashboard.routes import dashboard_bp
    from .verificacoes.routes import verificacoes_bp
    from .mensagens.routes import mensagens_bp
    from .configuracoes.routes import configuracoes_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(verificacoes_bp)
    app.register_blueprint(mensagens_bp)
    app.register_blueprint(configuracoes_bp)

    # DB + seed
    with app.app_context():
        db.create_all()
        _seed_admin()
        _seed_default_config()

    # Scheduler (daily at 07:00)
    from .scheduler import init_scheduler
    init_scheduler(app)

    return app


def _seed_admin():
    from .models import User
    if not User.query.first():
        admin = User(
            username='admin',
            password_hash=bcrypt.generate_password_hash('admin123').decode('utf-8')
        )
        db.session.add(admin)
        db.session.commit()
        print('[SEED] Usuário admin criado → admin / admin123')


def _seed_default_config():
    from .models import Config
    defaults = {
        'saber_url': 'https://adtalento.com/websiteSaber',
        'saber_usuario': '',
        'saber_senha': '',
        'restaurante': 'NPN',
        'telefone': '',
    }
    changed = False
    for chave, valor in defaults.items():
        if not Config.query.filter_by(chave=chave).first():
            db.session.add(Config(chave=chave, valor=valor))
            changed = True
    if changed:
        db.session.commit()
