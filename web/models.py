from flask_login import UserMixin
from datetime import datetime, timezone
import json
from .extensions import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    permissions = db.Column(db.Text, default='[]')  # JSON list of permission keys

    def has_perm(self, perm):
        """Admin tem todas as permissões. Outros verificam na lista."""
        if self.is_admin:
            return True
        return perm in self.get_perms()

    def get_perms(self):
        try:
            return json.loads(self.permissions or '[]')
        except Exception:
            return []

    def set_perms(self, perm_list):
        self.permissions = json.dumps(list(set(perm_list)))

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'is_admin': self.is_admin,
            'ativo': self.ativo,
            'permissions': self.get_perms(),
        }

    # Flask-Login: desativar conta impede sessão
    @property
    def is_active(self):
        return bool(self.ativo)

    def __repr__(self):
        return f'<User {self.username}>'


class Verificacao(db.Model):
    __tablename__ = 'verificacoes'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    cargo = db.Column(db.String(200))
    atividade = db.Column(db.String(300))
    data_verificacao = db.Column(db.Date)
    status = db.Column(db.String(50), default='pendente')   # pendente / apto
    origem = db.Column(db.String(20), default='manual')     # manual / automatico
    criado_em = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'cargo': self.cargo,
            'atividade': self.atividade,
            'data_verificacao': self.data_verificacao.strftime('%d/%m/%Y') if self.data_verificacao else '',
            'status': self.status,
            'origem': self.origem,
            'criado_em': self.criado_em.strftime('%d/%m/%Y %H:%M') if self.criado_em else '',
        }


class Mensagem(db.Model):
    __tablename__ = 'mensagens'
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    conteudo = db.Column(db.Text, nullable=False)
    criado_em = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'titulo': self.titulo,
            'conteudo': self.conteudo,
            'criado_em': self.criado_em.strftime('%d/%m/%Y %H:%M') if self.criado_em else '',
        }


class Config(db.Model):
    __tablename__ = 'config'
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(100), unique=True, nullable=False)
    valor = db.Column(db.Text, default='')

    @staticmethod
    def get(chave, default=''):
        item = Config.query.filter_by(chave=chave).first()
        return item.valor if item else default

    @staticmethod
    def set(chave, valor):
        item = Config.query.filter_by(chave=chave).first()
        if item:
            item.valor = valor
        else:
            item = Config(chave=chave, valor=valor)
            db.session.add(item)


class LogAutomacao(db.Model):
    __tablename__ = 'logs_automacao'
    id = db.Column(db.Integer, primary_key=True)
    executado_em = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(20))       # sucesso / erro / executando
    total_coletados = db.Column(db.Integer, default=0)
    mensagem = db.Column(db.Text)

    def to_dict(self):
        return {
            'id': self.id,
            'executado_em': self.executado_em.strftime('%d/%m/%Y %H:%M:%S') if self.executado_em else '',
            'status': self.status,
            'total_coletados': self.total_coletados,
            'mensagem': self.mensagem,
        }
