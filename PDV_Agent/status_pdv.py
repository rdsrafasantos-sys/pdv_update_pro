"""
===============================================================
  PDV Status - Monitor de atualização
  Fica rodando invisível na inicialização do Windows.
  Quando detecta C:\PDVAgent\progresso.json, abre a janela.
  Fecha sozinho ao concluir.
===============================================================
"""

import tkinter as tk
import threading
import json
import time
import os
import sys
import subprocess

PROGRESSO_FILE = r"C:\PDVAgent\progresso.json"
POLL_MS        = 800
VRCHECKOUT_EXE = r"C:\vrpdv\vrcheckout.exe"
VRPDV_DIR      = r"C:\vrpdv"

ETAPAS_IDX = {
    "Encerrando processos": 0,
    "Parando serviços":     1,
    "Realizando backup":    2,
    "Descompactando":       3,
    "Iniciando serviços":   4,
    "Iniciando vrcheckout": 5,
}

ETAPAS_LABELS = [
    "Encerrando processos",
    "Parando serviços",
    "Realizando backup",
    "Descompactando arquivos",
    "Iniciando serviços",
    "Iniciando PDV",
]

ICONES_ETAPA = {
    "Encerrando processos": ("🔴", "Finalizando processos ativos..."),
    "Parando serviços":     ("⏹",  "Parando serviços Mongo..."),
    "Realizando backup":    ("💾",  "Fazendo cópia de segurança..."),
    "Descompactando":       ("📦",  "Extraindo arquivos do .zip..."),
    "Iniciando serviços":   ("▶",   "Iniciando serviços Mongo..."),
    "Iniciando vrcheckout": ("🖥",  "Abrindo o sistema PDV..."),
}

# Cores
BG      = "#0a0e1a"
SURFACE = "#1a1d27"
SURFACE2= "#22263a"
BORDER  = "#2e3248"
ACCENT  = "#4f8ef7"
GREEN   = "#22c55e"
RED     = "#ef4444"
TEXT    = "#e2e8f0"
TEXT2   = "#64748b"

class StatusApp:
    def __init__(self, root):
        self.root         = root
        self.janela_aberta = False
        self.ultimo_status = None

        # Janela começa oculta
        self.root.withdraw()
        self.root.title("PDV - Atualização em andamento")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self._drag_x = self._drag_y = 0

        w, h = 520, 580
        sw   = root.winfo_screenwidth()
        sh   = root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self.progresso_atual = 0
        self.progresso_alvo  = 0
        self.bar_cor         = ACCENT

        self._build_ui()
        self._animar_progresso()
        self._monitorar()

    # ──────────────────────────────────────────
    # MONITORAMENTO DO ARQUIVO DE SINAL
    # ──────────────────────────────────────────
    def _monitorar(self):
        def checar():
            while True:
                try:
                    if os.path.exists(PROGRESSO_FILE):
                        with open(PROGRESSO_FILE, "r", encoding="utf-8") as f:
                            dados = json.load(f)

                        status = dados.get("status", "idle")

                        # Mostra a janela quando começa
                        if status == "updating" and not self.janela_aberta:
                            self.janela_aberta = True
                            self.root.after(0, self.root.deiconify)

                        # Atualiza UI se mudou
                        chave = json.dumps(dados, sort_keys=True)
                        if chave != self.ultimo_status:
                            self.ultimo_status = chave
                            self.root.after(0, lambda d=dados: self.atualizar_ui(d))

                        # Quando concluiu com sucesso: abre o PDV e fecha
                        if status == "success":
                            self.root.after(0, self._abrir_pdv)
                            time.sleep(4)
                            try:
                                os.remove(PROGRESSO_FILE)
                            except:
                                pass
                            self.root.after(0, self.root.destroy)
                            return

                        # Quando deu erro: só fecha após 6s sem abrir o PDV
                        if status == "error":
                            time.sleep(6)
                            try:
                                os.remove(PROGRESSO_FILE)
                            except:
                                pass
                            self.root.after(0, self.root.destroy)
                            return
                    else:
                        # Reseta estado se arquivo sumiu
                        if self.janela_aberta:
                            self.janela_aberta = False
                            self.root.after(0, self.root.withdraw)

                except Exception:
                    pass
                time.sleep(POLL_MS / 1000)

        threading.Thread(target=checar, daemon=True).start()

    # ──────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────
    def _build_ui(self):
        # Barra de título arrastável
        tbar = tk.Frame(self.root, bg=SURFACE, height=40)
        tbar.pack(fill="x")
        tbar.bind("<ButtonPress-1>", lambda e: setattr(self, '_drag_x', e.x) or setattr(self, '_drag_y', e.y))
        tbar.bind("<B1-Motion>",     self._do_drag)
        tk.Label(tbar, text="⚡  PDV Updater", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=16, pady=8)
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=28, pady=24)

        # Ícone principal
        self.lbl_icone = tk.Label(body, text="⚡", bg=BG, fg=ACCENT,
                                   font=("Segoe UI", 36))
        self.lbl_icone.pack(pady=(0, 6))

        self.lbl_titulo = tk.Label(body, text="Atualização do PDV",
                                    bg=BG, fg=TEXT, font=("Segoe UI", 16, "bold"))
        self.lbl_titulo.pack()

        self.lbl_sub = tk.Label(body, text="Aguardando início...",
                                 bg=BG, fg=TEXT2, font=("Segoe UI", 10))
        self.lbl_sub.pack(pady=(4, 20))

        # Card etapa atual
        card = tk.Frame(body, bg=SURFACE, highlightthickness=1,
                        highlightbackground=BORDER)
        card.pack(fill="x", pady=(0, 14))

        row = tk.Frame(card, bg=SURFACE)
        row.pack(fill="x", padx=16, pady=14)

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

        # Barra de progresso
        bar_frame = tk.Frame(card, bg=SURFACE)
        bar_frame.pack(fill="x", padx=16, pady=(0, 14))

        self.canvas_bar = tk.Canvas(bar_frame, height=10, bg=SURFACE2,
                                     highlightthickness=0)
        self.canvas_bar.pack(fill="x")
        self.canvas_bar.bind("<Configure>", lambda e: self._redraw_bar())

        # Lista de etapas
        steps = tk.Frame(body, bg=BG)
        steps.pack(fill="x")
        self.step_widgets = []
        for i, label in enumerate(ETAPAS_LABELS):
            r = tk.Frame(steps, bg=SURFACE2, highlightthickness=1,
                         highlightbackground=BORDER)
            r.pack(fill="x", pady=2)
            n = tk.Label(r, text=str(i+1), bg=SURFACE2, fg=TEXT2,
                         font=("Segoe UI", 9, "bold"), width=3, height=2)
            n.pack(side="left")
            l = tk.Label(r, text=label, bg=SURFACE2, fg=TEXT2,
                         font=("Segoe UI", 10), anchor="w")
            l.pack(side="left", fill="x", expand=True)
            c = tk.Label(r, text="", bg=SURFACE2, fg=GREEN,
                         font=("Segoe UI", 11, "bold"), width=3)
            c.pack(side="right")
            self.step_widgets.append({"row": r, "num": n, "lbl": l, "check": c})

    def _do_drag(self, e):
        x = self.root.winfo_x() + (e.x - self._drag_x)
        y = self.root.winfo_y() + (e.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def _redraw_bar(self):
        self.canvas_bar.delete("all")
        w = self.canvas_bar.winfo_width()
        self.canvas_bar.create_rectangle(0, 0, w, 10, fill=SURFACE2, outline="")
        fw = int(w * self.progresso_atual / 100)
        if fw > 0:
            self.canvas_bar.create_rectangle(0, 0, fw, 10,
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
        status    = dados.get("status", "idle")
        etapa     = dados.get("etapa", "")
        progresso = dados.get("progresso", 0)
        erro      = dados.get("erro", "")
        inicio    = dados.get("inicio")

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
                sw["row"].config(bg="#0f2a1a", highlightbackground="#1a3a2a")
                sw["num"].config(bg="#0f2a1a", fg=GREEN)
                sw["lbl"].config(bg="#0f2a1a", fg="#86efac")
                sw["check"].config(bg="#0f2a1a", text="✓", fg=GREEN)
            elif i == idx:
                sw["row"].config(bg="#0f1e3a", highlightbackground=ACCENT)
                sw["num"].config(bg="#0f1e3a", fg=ACCENT)
                sw["lbl"].config(bg="#0f1e3a", fg="#93c5fd")
                sw["check"].config(bg="#0f1e3a", text="→", fg=ACCENT)
            else:
                sw["row"].config(bg=SURFACE2, highlightbackground=BORDER)
                sw["num"].config(bg=SURFACE2, fg=TEXT2)
                sw["lbl"].config(bg=SURFACE2, fg=TEXT2)
                sw["check"].config(bg=SURFACE2, text="")

    def _mostrar_sucesso(self, inicio):
        self.bar_cor = GREEN
        self.progresso_alvo = 100
        self.lbl_icone.config(text="✅", fg=GREEN)
        self.lbl_titulo.config(text="Atualização Concluída!", fg=GREEN)
        self.lbl_etapa_icone.config(text="✅")
        self.lbl_etapa_nome.config(text="Atualizado com sucesso!")
        self.lbl_etapa_desc.config(text="PDV atualizado e reiniciado")
        self.lbl_pct.config(text="100%", fg=GREEN)
        for sw in self.step_widgets:
            sw["row"].config(bg="#0f2a1a", highlightbackground="#1a3a2a")
            sw["num"].config(bg="#0f2a1a", fg=GREEN)
            sw["lbl"].config(bg="#0f2a1a", fg="#86efac")
            sw["check"].config(bg="#0f2a1a", text="✓", fg=GREEN)
        if inicio:
            try:
                from datetime import datetime
                diff = int((datetime.now() -
                            datetime.strptime(inicio, "%Y-%m-%d %H:%M:%S")).total_seconds())
                m, s = diff // 60, diff % 60
                self.lbl_sub.config(
                    text=f"Concluído em {m}min {s}s" if m else f"Concluído em {s}s",
                    fg=ACCENT)
            except:
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

    def _abrir_pdv(self):
        """Abre o vrcheckout.exe — executado pelo usuário logado, não pelo serviço."""
        try:
            if os.path.exists(VRCHECKOUT_EXE):
                subprocess.Popen(
                    [VRCHECKOUT_EXE],
                    cwd=VRPDV_DIR
                )
                import logging
                logging.getLogger(__name__).info("vrcheckout.exe aberto pelo status_pdv.")
            else:
                import logging
                logging.getLogger(__name__).warning(f"vrcheckout.exe nao encontrado: {VRCHECKOUT_EXE}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Erro ao abrir vrcheckout: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app  = StatusApp(root)
    root.mainloop()
