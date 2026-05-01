from flask import Blueprint

analise_bp = Blueprint('analise', __name__, url_prefix='/analise')

from . import routes  # noqa: F401, E402
