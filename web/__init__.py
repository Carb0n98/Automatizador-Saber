import os
from datetime import timedelta
from flask import Flask
from .extensions import db, login_manager, bcrypt


def create_app():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__, template_folder='templates')

    # Config
    secret_key = os.environ.get('SECRET_KEY', '').strip()
    if not secret_key or secret_key == 'dev-fallback-altere-em-producao':
        import secrets
        secret_key = secrets.token_hex(32)
        print('[SECURITY] SECRET_KEY gerada automaticamente. Defina SECRET_KEY no .env para produção.')
    app.config['SECRET_KEY'] = secret_key

    # Session security
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # SESSION_COOKIE_SECURE = True em produção (HTTPS)
    if os.environ.get('FLASK_ENV') == 'production':
        app.config['SESSION_COOKIE_SECURE'] = True

    # Database: prioriza env var, senão usa /app/instance/
    instance_dir = os.path.join(base_dir, 'instance')
    os.makedirs(instance_dir, exist_ok=True)
    default_db = f'sqlite:///{os.path.join(instance_dir, "verificacoes.db")}'
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', default_db) or default_db
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Extensions
    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Faça login para acessar o painel.'
    login_manager.login_message_category = 'warning'
    login_manager.session_protection = 'strong'

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
    from .usuarios.routes import usuarios_bp
    from .whatsapp.routes import whatsapp_bp
    from .logs.routes import logs_bp
    from .analise.routes import analise_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(verificacoes_bp)
    app.register_blueprint(mensagens_bp)
    app.register_blueprint(configuracoes_bp)
    app.register_blueprint(usuarios_bp)
    app.register_blueprint(whatsapp_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(analise_bp)

    # DB + migrações automáticas + seed
    with app.app_context():
        db.create_all()
        from .utils import migrate_user_columns
        migrate_user_columns(db)
        _create_indices(db)
        _migrate_dedup(db)
        _seed_admin()
        _seed_default_config()

    # Handler global: persiste exceções não tratadas no painel de logs
    @app.errorhandler(Exception)
    def handle_unhandled_exception(e):
        from werkzeug.exceptions import HTTPException
        from flask import request as req

        # Erros HTTP (4xx, 5xx do Werkzeug) — deixa o Flask renderizar normalmente
        if isinstance(e, HTTPException):
            return e

        # Apenas exceções Python não tratadas (bugs reais) chegam aqui
        try:
            from .logger import log_error
            log_error(
                f'{req.method} {req.path} -> {type(e).__name__}: {e}',
                exc=e, origem='backend'
            )
        except Exception:
            pass
        raise e

    # Scheduler (daily at 07:00)
    from .scheduler import init_scheduler
    init_scheduler(app)

    return app


def _seed_admin():
    from .models import User
    if not User.query.first():
        admin = User(
            username='admin',
            password_hash=bcrypt.generate_password_hash('admin123').decode('utf-8'),
            is_admin=True,
            ativo=True,
        )
        db.session.add(admin)
        db.session.commit()
        print('[SEED] Usuario admin criado -> admin / admin123')
    else:
        # Garante que o primeiro admin existente seja is_admin=True
        first = User.query.first()
        if not first.is_admin:
            first.is_admin = True
            db.session.commit()
            print(f'[SEED] {first.username} promovido a admin')


def _seed_default_config():
    from .models import Config
    defaults = {
        'saber_url': 'https://adtalento.com/websiteSaber',
        'saber_usuario': '',
        'saber_senha': '',
        'restaurante': 'NPN',
        'telefone': '',
        'timezone': 'America/Sao_Paulo',
    }
    changed = False
    for chave, valor in defaults.items():
        if not Config.query.filter_by(chave=chave).first():
            db.session.add(Config(chave=chave, valor=valor))
            changed = True
    if changed:
        db.session.commit()


def _create_indices(db):
    """Cria índices otimizados para queries mensais e de status."""
    try:
        from sqlalchemy import text
        with db.engine.connect() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_verif_data_status ON verificacoes (data_verificacao, status)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_verif_status ON verificacoes (status)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_verif_nome_data_ativ ON verificacoes (nome, data_verificacao, atividade)"))
            conn.commit()
    except Exception as e:
        print(f"[MIGRATE] Aviso ao criar índice: {e}")


def _migrate_dedup(db):
    """
    Migração automática:
    1. Remove duplicatas existentes no banco (mantém o registro com menor ID)
    2. Cria constraint UNIQUE se não existir
    3. Adiciona novos campos de segurança ao User
    """
    from sqlalchemy import text
    try:
        with db.engine.connect() as conn:
            # ─── Limpar duplicatas existentes ─────────────────────────────
            # Encontra IDs duplicados (mantém o menor ID de cada grupo)
            dupes = conn.execute(text("""
                SELECT id FROM verificacoes
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM verificacoes
                    GROUP BY nome, data_verificacao, COALESCE(atividade, '')
                )
            """)).fetchall()

            if dupes:
                ids_to_delete = [row[0] for row in dupes]
                # SQLite não suporta DELETE com IN de muitos valores facilmente,
                # então deletamos em lotes
                for i in range(0, len(ids_to_delete), 50):
                    batch = ids_to_delete[i:i+50]
                    placeholders = ','.join(str(x) for x in batch)
                    conn.execute(text(f"DELETE FROM verificacoes WHERE id IN ({placeholders})"))
                conn.commit()
                print(f'[MIGRATE] {len(ids_to_delete)} registro(s) duplicado(s) removido(s).')

            # ─── Criar UNIQUE INDEX ───────────────────────────────────────
            try:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_verif_nome_data_ativ "
                    "ON verificacoes (nome, data_verificacao, COALESCE(atividade, ''))"
                ))
                conn.commit()
            except Exception:
                pass  # Já existe

            # ─── Migrar campos de segurança do User ──────────────────────
            new_cols = [
                ("ultimo_login",     "DATETIME"),
                ("tentativas_login", "INTEGER DEFAULT 0 NOT NULL"),
                ("bloqueado_ate",    "DATETIME"),
            ]
            for col_name, col_def in new_cols:
                try:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))
                    conn.commit()
                    print(f'[MIGRATE] Coluna adicionada: users.{col_name}')
                except Exception:
                    pass  # Coluna já existe

    except Exception as e:
        print(f'[MIGRATE] Aviso na migração de dedup: {e}')
