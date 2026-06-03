"""
Constantes centralizadas do sistema.

Toda lógica que determina se um funcionário é "Verificado" deve usar
VERIFICADO_STATUSES ou is_verificado() para manter consistência.
"""

# Status no banco que contam como "Verificados" na dashboard e relatórios
VERIFICADO_STATUSES = ['apto', 'parcialmente_apto']

# Mapeamento do status bruto do SABER → valor no banco
SABER_STATUS_MAP = {
    'APTO': 'apto',
    'PARCIALMENTE APTO': 'parcialmente_apto',
}


def is_verificado(status: str) -> bool:
    """Retorna True se o status é considerado 'Verificado'."""
    return status in VERIFICADO_STATUSES
