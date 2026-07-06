"""
PDV Status v2.0 - Monitor de atualização
Roda permanentemente na sessão do usuário (Run key).
- Single instance: nunca roda duplicado
- Monitora C:\\PDVAgent\\progresso.json
- Mostra a janela quando atualização começa
- Abre o vrcheckout.exe ao concluir (na sessão do usuário!)
- Esconde e reseta — pronto para a próxima atualização
- NUNCA fecha sozinho
"""

import ctypes
import json
import logging
import os
import subprocess
import sys
import threading
import time
import tkinter as tk

PROGRESSO_FILE = r"C:\PDVAgent\progresso.json"
STATUS_LOG = r"C:\PDVAgent\status_pdv.log"
POLL_MS = 800
VRCHECKOUT_EXE = r"C:\vrpdv\vrcheckout.exe"
VRPDV_DIR = r"C:\vrpdv"

MUTEX_NAME = "PDVStatusAppMutex_2026"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(STATUS_LOG, encoding="utf-8")]
)
log = logging.getLogger(__name__)


def garantir_instancia_unica():
    """Usa named mutex do Windows para garantir única instância."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    err = ctypes.get_last_error()
    if h and err != 183:  # 183 = ERROR_ALREADY_EXISTS
        return h
    log.info("Outra instancia ja esta rodando (mutex). Saindo.")
    sys.exit(0)


ETAPAS_IDX = {
    "Encerrando processos":    0,
    "Parando servicos":        1,
    "Realizando backup":       2,
    "Descompactando":          3,
    "Verificando arquivos":    4,
    "Verificando processos":   4,
    "Iniciando servicos":      5,
    "Aguardando para abrir PDV": 6,
    "Iniciando vrcheckout":    6,
}

ETAPAS_LABELS = [
    "Encerrando processos",
    "Parando serviços",
    "Realizando backup",
    "Descompactando arquivos",
    "Verificando integridade",
    "Reiniciando serviços",
    "Abrindo o PDVPro",
]

ICONES_ETAPA = {
    "Encerrando processos":      ("🔴", "Finalizando processos ativos..."),
    "Parando servicos":          ("⏹",  "Parando serviços Mongo..."),
    "Realizando backup":         ("💾", "Fazendo cópia de segurança..."),
    "Descompactando":            ("📦", "Extraindo arquivos do .zip..."),
    "Verificando arquivos":      ("🔍", "Conferindo arquivos copiados..."),
    "Verificando processos":     ("🔍", "Garantindo processos encerrados..."),
    "Iniciando servicos":        ("▶",  "Reiniciando serviços Mongo..."),
    "Aguardando para abrir PDV": ("⏰", "Aguardando para abrir o PDV..."),
    "Iniciando vrcheckout":      ("🖥",  "Abrindo o sistema PDV..."),
}

# Cores — Tema Laranja VR
BG       = "#0d0800"
SURFACE  = "#1a1200"
SURFACE2 = "#211800"
BORDER   = "#3d2800"
ACCENT    = "#f97316"   # laranja principal
RED       = "#ef4444"
TEXT      = "#ffffff"
TEXT2     = "#a07848"
ACTIVE_BG = "#2a1000"   # etapa em andamento
ACTIVE_FG = "#fdba74"
DONE_BG   = "#1a0900"   # etapa concluída — laranja escuro (era verde)
DONE_FG   = "#fdba74"   # texto de etapa concluída
DONE_BDR  = "#3d1800"   # borda de etapa concluída
WM_COLOR  = "#2a1400"   # VR watermark


class StatusApp:
    def __init__(self, root):
        self.root = root
        self.janela_aberta = False
        self.ultimo_status = None

        self.root.withdraw()
        self.root.title("PDVPro - Atualização em andamento")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self._drag_x = self._drag_y = 0

        w, h = 520, 720
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self.progresso_atual = 0
        self.progresso_alvo = 0
        self.bar_cor = ACCENT

        self._build_ui()
        self._animar_progresso()
        self._monitorar()
        log.info("StatusApp iniciado e monitorando.")

    # ──────────────────────────────────────────
    # MONITORAMENTO — loop infinito, NUNCA retorna
    # ──────────────────────────────────────────
    def _monitorar(self):
        def checar():
            while True:
                try:
                    if os.path.exists(PROGRESSO_FILE):
                        with open(PROGRESSO_FILE, "r", encoding="utf-8") as f:
                            dados = json.load(f)

                        status = dados.get("status", "idle")

                        if status == "updating" and not self.janela_aberta:
                            self.janela_aberta = True
                            log.info("Atualizacao detectada — mostrando janela.")
                            self.root.after(0, self.root.deiconify)
                            self.root.after(0, self.root.lift)

                        chave = json.dumps(dados, sort_keys=True)
                        if chave != self.ultimo_status:
                            self.ultimo_status = chave
                            self.root.after(0, lambda d=dados: self.atualizar_ui(d))

                        if status == "success":
                            log.info("Sucesso detectado — abrindo PDV.")
                            self.root.after(0, self._abrir_pdv)
                            time.sleep(5)
                            self._limpar_e_resetar()
                            continue

                        if status == "error":
                            log.info("Erro detectado — aguardando 8s.")
                            time.sleep(8)
                            self._limpar_e_resetar()
                            continue
                    else:
                        if self.janela_aberta:
                            self.janela_aberta = False
                            self.ultimo_status = None
                            self.root.after(0, self.root.withdraw)

                except Exception as e:
                    log.warning(f"Erro no monitor: {e}")
                time.sleep(POLL_MS / 1000)

        threading.Thread(target=checar, daemon=True).start()

    def _limpar_e_resetar(self):
        """Remove o progresso.json, reseta a UI e esconde — pronto p/ próxima."""
        try:
            os.remove(PROGRESSO_FILE)
        except Exception:
            pass
        self.janela_aberta = False
        self.ultimo_status = None
        self.root.after(0, self._resetar_ui)
        self.root.after(0, self.root.withdraw)
        log.info("UI resetada — monitorando proxima atualizacao.")

    # ──────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────
    def _build_ui(self):
        # Topbar — linha de título arrastável
        tbar = tk.Frame(self.root, bg=SURFACE, height=40)
        tbar.pack(fill="x")
        tbar.bind("<ButtonPress-1>",
                  lambda e: setattr(self, '_drag_x', e.x) or setattr(self, '_drag_y', e.y))
        tbar.bind("<B1-Motion>", self._do_drag)
        tk.Label(tbar, text="VR  Atualização PDVPro", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=16, pady=8)
        tk.Frame(self.root, bg=ACCENT, height=2).pack(fill="x")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=28, pady=14)

        # Ícone VR + título
        self.lbl_icone = tk.Label(body, text="VR", bg=BG, fg=ACCENT,
                                   font=("Segoe UI", 38, "bold"))
        self.lbl_icone.pack(pady=(0, 4))

        self.lbl_titulo = tk.Label(body, text="Atualização do PDVPro",
                                    bg=BG, fg=TEXT, font=("Segoe UI", 16, "bold"))
        self.lbl_titulo.pack()

        self.lbl_sub = tk.Label(body, text="Aguardando início...",
                                 bg=BG, fg=TEXT2, font=("Segoe UI", 10))
        self.lbl_sub.pack(pady=(4, 12))

        # Card de etapa atual + barra de progresso
        card = tk.Frame(body, bg=SURFACE, highlightthickness=1,
                         highlightbackground=BORDER)
        card.pack(fill="x", pady=(0, 12))

        row = tk.Frame(card, bg=SURFACE)
        row.pack(fill="x", padx=16, pady=12)

        self.lbl_etapa_icone = tk.Label(row, text="⏳", bg=SURFACE, fg=TEXT,
                                         font=("Segoe UI", 20), width=3)
        self.lbl_etapa_icone.pack(side="left")

        txt = tk.Frame(row, bg=SURFACE)
        txt.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.lbl_etapa_nome = tk.Label(txt, text="Aguardando...", bg=SURFACE,
                                        fg=TEXT, anchor="w",
                                        font=("Segoe UI", 11, "bold"))
        self.lbl_etapa_nome.pack(fill="x")

        self.lbl_etapa_desc = tk.Label(txt, text="Processo será iniciado em breve",
                                        bg=SURFACE, fg=TEXT2, anchor="w",
                                        font=("Segoe UI", 9))
        self.lbl_etapa_desc.pack(fill="x")

        self.lbl_pct = tk.Label(row, text="0%", bg=SURFACE, fg=ACCENT,
                                 font=("Segoe UI", 18, "bold"), width=5, anchor="e")
        self.lbl_pct.pack(side="right")

        bar_frame = tk.Frame(card, bg=SURFACE)
        bar_frame.pack(fill="x", padx=16, pady=(0, 12))

        self.canvas_bar = tk.Canvas(bar_frame, height=8, bg=SURFACE2,
                                     highlightthickness=0)
        self.canvas_bar.pack(fill="x")
        self.canvas_bar.bind("<Configure>", lambda e: self._redraw_bar())

        # Lista de etapas (todas as 7)
        steps = tk.Frame(body, bg=BG)
        steps.pack(fill="x")
        self.step_widgets = []
        for i, label in enumerate(ETAPAS_LABELS):
            r = tk.Frame(steps, bg=SURFACE2, highlightthickness=1,
                         highlightbackground=BORDER)
            r.pack(fill="x", pady=2)
            n = tk.Label(r, text=str(i + 1), bg=SURFACE2, fg=TEXT2,
                         font=("Segoe UI", 9, "bold"), width=3, height=1)
            n.pack(side="left")
            lbl = tk.Label(r, text=label, bg=SURFACE2, fg=TEXT2,
                           font=("Segoe UI", 10), anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            chk = tk.Label(r, text="", bg=SURFACE2, fg=ACCENT,
                           font=("Segoe UI", 11, "bold"), width=3)
            chk.pack(side="right")
            self.step_widgets.append({"row": r, "num": n, "lbl": lbl, "check": chk})

        # Marca d'água VR — fica atrás de todo o conteúdo
        wm = tk.Label(body, text="VR", bg=BG, fg=WM_COLOR,
                      font=("Segoe UI", 130, "bold"))
        wm.place(relx=0.5, rely=0.38, anchor="center")
        wm.lower()

    def _do_drag(self, e):
        x = self.root.winfo_x() + (e.x - self._drag_x)
        y = self.root.winfo_y() + (e.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def _redraw_bar(self):
        self.canvas_bar.delete("all")
        w = self.canvas_bar.winfo_width()
        self.canvas_bar.create_rectangle(0, 0, w, 8, fill=SURFACE2, outline="")
        fw = int(w * self.progresso_atual / 100)
        if fw > 0:
            self.canvas_bar.create_rectangle(0, 0, fw, 8,
                                              fill=self.bar_cor, outline="")

    def _animar_progresso(self):
        if self.progresso_atual < self.progresso_alvo:
            self.progresso_atual = min(self.progresso_atual + 1, self.progresso_alvo)
            self._redraw_bar()
        self.root.after(18, self._animar_progresso)

    # ──────────────────────────────────────────
    # ATUALIZAR UI
    # ──────────────────────────────────────────
    def atualizar_ui(self, dados):
        status   = dados.get("status", "idle")
        etapa    = dados.get("etapa", "")
        progresso = dados.get("progresso", 0)
        erro     = dados.get("erro", "")
        inicio   = dados.get("inicio")

        self.progresso_alvo = progresso

        if status == "success":
            self._mostrar_sucesso(inicio)
            return
        if status == "error":
            self._mostrar_erro(erro)
            return

        icone, desc = ICONES_ETAPA.get(etapa, ("⏳", "Processando..."))
        self.lbl_etapa_icone.config(text=icone)
        self.lbl_etapa_nome.config(text=etapa or "Processando...")
        self.lbl_etapa_desc.config(text=desc)
        self.lbl_pct.config(text=f"{progresso}%", fg=ACCENT)

        idx = ETAPAS_IDX.get(etapa, -1)
        for i, sw in enumerate(self.step_widgets):
            if i < idx:
                sw["row"].config(bg=DONE_BG,    highlightbackground=DONE_BDR)
                sw["num"].config(bg=DONE_BG,    fg=ACCENT)
                sw["lbl"].config(bg=DONE_BG,    fg=DONE_FG)
                sw["check"].config(bg=DONE_BG,  text="✓", fg=ACCENT)
            elif i == idx:
                sw["row"].config(bg=ACTIVE_BG,  highlightbackground=ACCENT)
                sw["num"].config(bg=ACTIVE_BG,  fg=ACCENT)
                sw["lbl"].config(bg=ACTIVE_BG,  fg=ACTIVE_FG)
                sw["check"].config(bg=ACTIVE_BG, text="→", fg=ACCENT)
            else:
                sw["row"].config(bg=SURFACE2,   highlightbackground=BORDER)
                sw["num"].config(bg=SURFACE2,   fg=TEXT2)
                sw["lbl"].config(bg=SURFACE2,   fg=TEXT2)
                sw["check"].config(bg=SURFACE2, text="")

    def _mostrar_sucesso(self, inicio):
        self.bar_cor = ACCENT
        self.progresso_alvo = 100
        self.lbl_icone.config(text="VR", fg=ACCENT)
        self.lbl_titulo.config(text="Atualização Concluída!", fg=ACCENT)
        self.lbl_etapa_icone.config(text="✅")
        self.lbl_etapa_nome.config(text="Atualizado com sucesso!")
        self.lbl_etapa_desc.config(text="Abrindo o PDVPro...")
        self.lbl_pct.config(text="100%", fg=ACCENT)
        for sw in self.step_widgets:
            sw["row"].config(bg=DONE_BG,   highlightbackground=DONE_BDR)
            sw["num"].config(bg=DONE_BG,   fg=ACCENT)
            sw["lbl"].config(bg=DONE_BG,   fg=DONE_FG)
            sw["check"].config(bg=DONE_BG, text="✓", fg=ACCENT)
        if inicio:
            try:
                from datetime import datetime
                diff = int((datetime.now() -
                            datetime.strptime(inicio, "%Y-%m-%d %H:%M:%S")).total_seconds())
                m, s = diff // 60, diff % 60
                self.lbl_sub.config(
                    text=f"Concluído em {m}min {s}s" if m else f"Concluído em {s}s",
                    fg=ACCENT)
            except Exception:
                self.lbl_sub.config(text="Concluído!", fg=ACCENT)

    def _mostrar_erro(self, msg):
        self.bar_cor = RED
        self.lbl_icone.config(text="❌", fg=RED)
        self.lbl_titulo.config(text="Erro na Atualização", fg=RED)
        self.lbl_etapa_icone.config(text="❌")
        self.lbl_etapa_nome.config(text="Falha na atualização")
        self.lbl_etapa_desc.config(text=msg or "Erro desconhecido")
        self.lbl_pct.config(fg=RED)
        self.lbl_sub.config(text="Contate o suporte técnico", fg=RED)

    def _resetar_ui(self):
        self.progresso_atual = 0
        self.progresso_alvo = 0
        self.bar_cor = ACCENT
        self.lbl_icone.config(text="VR", fg=ACCENT)
        self.lbl_titulo.config(text="Atualização do PDVPro", fg=TEXT)
        self.lbl_sub.config(text="Aguardando início...", fg=TEXT2)
        self.lbl_etapa_icone.config(text="⏳")
        self.lbl_etapa_nome.config(text="Aguardando...")
        self.lbl_etapa_desc.config(text="Processo será iniciado em breve")
        self.lbl_pct.config(text="0%", fg=ACCENT)
        for sw in self.step_widgets:
            sw["row"].config(bg=SURFACE2,   highlightbackground=BORDER)
            sw["num"].config(bg=SURFACE2,   fg=TEXT2)
            sw["lbl"].config(bg=SURFACE2,   fg=TEXT2)
            sw["check"].config(bg=SURFACE2, text="")
        self._redraw_bar()

    def _abrir_pdv(self):
        """Abre o vrcheckout.exe NA SESSAO DO USUARIO (este processo)."""
        try:
            if os.path.exists(VRCHECKOUT_EXE):
                subprocess.Popen([VRCHECKOUT_EXE], cwd=VRPDV_DIR)
                log.info("vrcheckout.exe aberto na sessao do usuario.")
            else:
                log.warning(f"vrcheckout.exe nao encontrado: {VRCHECKOUT_EXE}")
        except Exception as e:
            log.error(f"Erro ao abrir vrcheckout: {e}")


def main():
    # DPI awareness para Windows 10/11
    try:
        import ctypes as _ct
        _ct.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes as _ct
            _ct.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    _lock = garantir_instancia_unica()
    root = tk.Tk()
    app = StatusApp(root)
    root.mainloop()
