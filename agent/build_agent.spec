# ===============================================================
#  build_agent.spec
#  Compila agente.exe e status_pdv.exe a partir de src/pdv_agent
#  Execute na pasta agent/: python -m PyInstaller build_agent.spec
# ===============================================================

block_cipher = None

# ── AGENTE ──────────────────────────────────────────────────
agente = Analysis(
    ['main_agent.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=['waitress', 'flask', 'lmdb', 'psutil', 'pefile'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'pdv_agent.status_app'],
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
    ['main_status.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=['tkinter', 'pdv_agent'],
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
