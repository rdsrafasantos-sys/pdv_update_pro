# ===============================================================
#  build_agent.spec
#  Compila agente.exe e status_pdv.exe juntos
#  Execute no Windows: python -m PyInstaller build_agent.spec
# ===============================================================

block_cipher = None

# ── AGENTE ──────────────────────────────────────────────────
agente = Analysis(
    ['agente.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['waitress', 'flask', 'lmdb'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
agente_pyz = PYZ(agente.pure, agente.zipped_data, cipher=block_cipher)
agente_exe = EXE(
    agente_pyz,
    agente.scripts,
    agente.binaries,
    agente.zipfiles,
    agente.datas,
    [],
    name='agente',
    debug=False,
    strip=False,
    upx=True,
    console=False,
    uac_admin=True,
)

# ── STATUS PDV ───────────────────────────────────────────────
status = Analysis(
    ['status_pdv.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['requests', 'tkinter'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
status_pyz = PYZ(status.pure, status.zipped_data, cipher=block_cipher)
status_exe = EXE(
    status_pyz,
    status.scripts,
    status.binaries,
    status.zipfiles,
    status.datas,
    [],
    name='status_pdv',
    debug=False,
    strip=False,
    upx=True,
    console=False,   # Sem janela de terminal — só a UI
    uac_admin=False,
)
