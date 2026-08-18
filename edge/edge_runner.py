#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# edge_runner.py — serviço de produção da borda Kalidash (Raspberry Pi 5, headless)
#
# Gerado a partir de KaliVision_Edge_Captura.ipynb (FONTE DA VERDADE). Helpers e
# constantes copiados VERBATIM do notebook — idênticos a backend/pipeline.py.
#
# Caminho ATIVO = MODO A: o Pi grava um clipe via ffmpeg (sem reencode) e faz
# upload pelo endpoint que já existe; o backend (GPU) roda YOLO/tracking/VLM.
# MODO B = preservado, porém INATIVO (só monta+loga o payload; /edge/ingest não
# existe ainda). MODO C não é incluído.
#
# Uso:
#   python edge_runner.py --check   # valida câmera/rede, salva 1 frame e sai
#   python edge_runner.py --once    # 1 ciclo do MODO atual e sai (debug)
#   python edge_runner.py           # loop de produção contínuo (systemd)
#
# Segredos SÓ via .env. Nada hardcoded. RTSP sempre TCP.
# ─────────────────────────────────────────────────────────────────────────────

# ── RTSP ROBUSTO (Célula 1): força TCP no FFmpeg ANTES de qualquer VideoCapture.
#    Não-negociável. UDP perde pacote e trava; TCP é estável.
import os
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# ── Imports base + logging (Célula 2) ────────────────────────────────────────
import sys
import json
import time
import base64
import shutil
import signal
import logging
import argparse
import subprocess
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from datetime import time as _dtime, date as _ddate

# fcntl é POSIX (o Pi é Linux). Só o lockfile de --capturar depende dele; se
# faltar (dev em Windows), o lock degrada para "sem trava" com aviso, e o resto
# do arquivo — inclusive as funções puras e os testes — continua importável.
try:
    import fcntl
except ImportError:  # pragma: no cover - só fora de POSIX
    fcntl = None

import cv2
import numpy as np
import requests

# ultralytics (YOLO) e supabase são importados SOB DEMANDA, dentro das funções
# que os usam (rodar_yolo e enviar_clipe). Assim o serviço sobe e roda o caminho
# ativo (MODO A) mesmo sem essas libs pesadas instaladas no Pi.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s · %(levelname)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("kalidash.edge")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG (Célula 3) — lê do .env quando houver, com fallback inline (placeholder).
# ─────────────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    log.warning("python-dotenv ausente — usando apenas valores inline (fallback).")


def _env(nome, padrao=None):
    """Lê variável do .env/ambiente; volta ao padrão inline se vazia/ausente."""
    v = os.getenv(nome)
    return v if v not in (None, "") else padrao


# ── CÂMERA / RTSP ────────────────────────────────────────────────────────────
# Exemplos Hikvision STANDALONE (câmera direta na rede):
#   main-stream:  rtsp://USUARIO:SENHA@IP_DA_CAMERA:554/Streaming/Channels/101
#   sub-stream :  rtsp://USUARIO:SENHA@IP_DA_CAMERA:554/Streaming/Channels/102
# ► Atrás de NVR/DVR: canal vira /Streaming/Channels/N01 (ex.: câmera 2 → 201/202).
# ► IMPORTANTE: se a senha tiver caracteres reservados de URL (@ : / ? #), faça
#   percent-encode — ex.: "Spectra@2026" → "Spectra%402026" — senão o ffmpeg lê o
#   host errado e a gravação trava. Detalhes no README_edge.md.
RTSP_URL_MAIN = _env("RTSP_URL_MAIN", "rtsp://USUARIO:SENHA@IP_DA_CAMERA:554/Streaming/Channels/101")
RTSP_URL_SUB  = _env("RTSP_URL_SUB",  "rtsp://USUARIO:SENHA@IP_DA_CAMERA:554/Streaming/Channels/102")
USAR_SUBSTREAM = _env("USAR_SUBSTREAM", "true").lower() == "true"   # sub = mais leve no Pi

# ── FLAGS-MESTRE ─────────────────────────────────────────────────────────────
MODO       = _env("MODO", "A")                              # "A" bridge/upload · "B" edge stub (inativo)
RODAR_YOLO = _env("RODAR_YOLO", "false").lower() == "true"  # só gate do YOLO do MODO B (A não usa YOLO)

# ── AMOSTRAGEM / MODELO (IDÊNTICOS ao pipeline da nuvem — NÃO redefinir valores) ─
INTERVALO_AMOSTRAGEM_S = 3.0
YOLO_MODEL     = "yolo11n-pose.pt"
YOLO_CONF_MIN  = 0.45
AREA_MIN_RATIO = 0.005
TRACKER_CONFIG = "botsort.yaml"

# ── DURAÇÕES ─────────────────────────────────────────────────────────────────
DURACAO_CLIPE_S   = int(_env("DURACAO_CLIPE_S", "120"))   # clipe do MODO A (gravar_clipe)
DURACAO_CAPTURA_S = int(_env("DURACAO_CAPTURA_S", "30"))  # janela de amostragem (MODO B)

# ── NUVEM / PLATAFORMA ───────────────────────────────────────────────────────
API_URL           = _env("API_URL", "http://localhost:8000")            # backend Kalidash
SUPABASE_URL      = _env("SUPABASE_URL", "https://SEU_PROJETO.supabase.co")
SUPABASE_ANON_KEY = _env("SUPABASE_ANON_KEY", "COLE_A_ANON_KEY_AQUI")    # anon, NUNCA service_role
EMAIL             = _env("EMAIL", "usuario@empresa.com")                 # mesmo usuário/empresa do app
SENHA             = _env("SENHA", "SUA_SENHA")
PROCESSO_ID       = _env("PROCESSO_ID", "00000000-0000-0000-0000-000000000000")  # uuid do processo
# Fase 48: sobe o vídeo DIRETO ao Supabase Storage (edge → Supabase), sem passar
# pelo backend do Render — corta o BANDWIDTH DE SAÍDA do Render (o vídeo é o
# maior tráfego e antes ia edge→Render→Supabase, contando egress no Render). O
# backend só troca 2 JSONs pequenos (pedir URL assinada + registrar na inbox).
# "off" volta ao upload clássico multipart pelo backend.
EDGE_UPLOAD_DIRETO = _env("EDGE_UPLOAD_DIRETO", "on").lower() in ("1", "on", "true", "sim")

# ── BORDA (MODO B, futuro) ───────────────────────────────────────────────────
EDGE_INGEST_URL = _env("EDGE_INGEST_URL", f"{API_URL}/edge/ingest")
DEVICE_ID       = _env("DEVICE_ID", "kalidash-edge-01")  # identifica este Pi/câmera

# ── RTSP robusto / poll (Célula 5 + produção) ────────────────────────────────
TIMEOUT_RTSP_MS = int(_env("TIMEOUT_RTSP_MS", "8000"))          # abertura/leitura (ms)
MAX_FALHAS_RECONEXAO = int(_env("MAX_FALHAS_RECONEXAO", "10"))  # leituras falhas antes de reabrir
BACKOFF_MAX_S = float(_env("BACKOFF_MAX_S", "15"))             # teto do backoff exponencial
POLL_TIMEOUT_S = int(_env("POLL_TIMEOUT_S", "900"))            # teto p/ o poll do job (não trava o ciclo)

# ── PASTAS DE SAÍDA ──────────────────────────────────────────────────────────
SAIDA_DIR  = Path(_env("SAIDA_DIR", "saida_edge"))
FRAMES_DIR = SAIDA_DIR / "frames"
SAIDA_DIR.mkdir(parents=True, exist_ok=True)
FRAMES_DIR.mkdir(parents=True, exist_ok=True)

# ── CAPTURA EM LOTE (--capturar) / PROCESSAMENTO NOTURNO (--processar) ────────
JANELA_CAPTURA_H = float(_env("JANELA_CAPTURA_H", "8"))   # duração da captura diária (h)
SEGMENTO_MIN     = int(_env("SEGMENTO_MIN", "10"))        # tamanho de cada segmento (min)
FILTRAR_PRESENCA = _env("FILTRAR_PRESENCA", "true").lower() == "true"  # só sobe segmentos com pessoa
RETENCAO_DIAS    = float(_env("RETENCAO_DIAS", "2"))      # apaga segmentos locais mais antigos que isso
MIN_GB_LIVRE     = float(_env("MIN_GB_LIVRE", "2"))       # piso de espaço livre no disco (limpa se cair abaixo)
SEG_DIR          = Path(_env("SEG_DIR", str(SAIDA_DIR / "seg")))
SEG_DIR.mkdir(parents=True, exist_ok=True)

# ── SINCRONIA MULTI-CÂMERA (--capturar) ─────────────────────────────────────
# Para cruzar as imagens depois, os segmentos das câmeras precisam cobrir a
# MESMA janela de relógio. Por isso cortamos os segmentos no RELÓGIO DE PAREDE
# (segment_atclocktime), não relativo ao instante em que cada stream conectou.
# Assim, independentemente de qual câmera conectou primeiro, o corte cai sempre
# em múltiplos de SEGMENTO_MIN (ex.: HH:00, HH:10, HH:20...) — os nomes de
# arquivo das duas câmeras passam a bater no mesmo segundo.
#  ⚠️ Com -c copy o corte só acontece no próximo KEYFRAME após a marca do
#     relógio. Para o corte ser exato, as DUAS câmeras precisam ter o MESMO
#     I-Frame Interval (GOP) pequeno (ex.: = FPS, 1 keyframe/seg) no painel da
#     Hikvision. Veja o README. Se não der pra mexer na câmera, ligue
#     CAPTURA_REENCODE=true (reencoda e força keyframe exato no corte — PESADO
#     na CPU do Pi; só para 1-2 câmeras em resolução modesta).
CAPTURA_SEGMENT_ATCLOCK = _env("CAPTURA_SEGMENT_ATCLOCK", "true").lower() == "true"
CAPTURA_REENCODE        = _env("CAPTURA_REENCODE", "false").lower() == "true"
CAPTURA_REENCODE_CRF    = int(_env("CAPTURA_REENCODE_CRF", "23"))
CAPTURA_REENCODE_PRESET = _env("CAPTURA_REENCODE_PRESET", "ultrafast")
# Se um ffmpeg de câmera morrer ANTES do fim da janela (stream caiu, câmera
# reiniciou), relançamos automaticamente até o fim — assim uma câmera instável
# não para silenciosa com 1 só segmento (era o caso da cam2). 0 = não relança.
CAPTURA_RELANCAR        = _env("CAPTURA_RELANCAR", "true").lower() == "true"
CAPTURA_RELANCO_MIN_S   = float(_env("CAPTURA_RELANCO_MIN_S", "10"))  # espera antes de relançar
# Detector de travamento: se a câmera CONECTOU mas parou de produzir frames
# (RTSP "vivo" sem dados — o caso clássico da cam2 que ficou com 1 segmento),
# o ffmpeg não morre, só para de escrever. Se o segmento em aberto não for
# tocado por CAPTURA_STALL_S segundos, matamos e relançamos.
CAPTURA_STALL_S         = float(_env("CAPTURA_STALL_S", "45"))

# ── PORTEIRO DE PRESENÇA (--processar) ──────────────────────────────────────
# Knobs MAIS PERMISSIVOS que os do pipeline da nuvem. Aqui a pergunta é só
# "tem alguém visível?" — falso positivo é leve (sobe um segmento a mais),
# falso negativo é grave (perde o turno). Com PORTEIRO_RECORTAR_ROI=true (o
# default), o YOLO roda SOMENTE no recorte do ROI, então a pessoa aparece em
# escala alta no detector — a confiança naturalmente sobe e os thresholds
# deixam de cortar gente real em chão de fábrica.
PORTEIRO_CONF_MIN       = float(_env("PORTEIRO_CONF_MIN", "0.20"))   # era 0.45 (YOLO_CONF_MIN)
PORTEIRO_AREA_MIN_RATIO = float(_env("PORTEIRO_AREA_MIN_RATIO", "0.005"))  # 0.5% do RECORTE
PORTEIRO_RECORTAR_ROI   = _env("PORTEIRO_RECORTAR_ROI", "true").lower() == "true"
PORTEIRO_LOG_DETALHES   = _env("PORTEIRO_LOG_DETALHES", "false").lower() == "true"

# ── PERFIL DE PRESENÇA (--processar, Fase 108) ──────────────────────────────
# ⭐ A TAREFA MAIS BÁSICA DA BORDA: tem gente no posto, e QUANDO.
#
# O backend erra muito o `posto_vazio`, e o motivo quase nunca é "não havia
# ninguém": é que a pessoa estava no quadro mas FORA do polígono desenhado, ou
# pequena demais para o detector da nuvem (que roda com conf 0.45 contra os
# 0.20 daqui, e sem o recorte do ROI que faz a pessoa aparecer em escala alta).
# Do jeito antigo, os dois casos chegavam ao backend com a mesma cara.
#
# A borda é o único lugar que pode separá-los, porque só ela tem os frames
# brutos. Então ela passa a medir presença em TRÊS níveis, do mesmo YOLO, sem
# nenhuma inferência a mais:
#
#   quadro      — alguém em qualquer lugar da área enviada
#   zona_larga  — qualquer parte do corpo dentro do polígono do posto
#   zona        — a ÂNCORA (topo do tronco) dentro do polígono
#
# `zona` é a MESMA regra que a nuvem usa para decidir. A diferença entre os
# três é o diagnóstico:
#
#   quadro=0                     → posto REALMENTE vazio. O backend acertou.
#   quadro alto + zona=0         → tem gente, e ela não está onde a zona foi
#                                  desenhada. Isso NÃO é posto vazio — é zona
#                                  errada, e é a maior parte dos erros.
#   zona_larga alto + zona baixa  → a pessoa trabalha na BORDA do polígono
#                                  (encosta com o braço, o tronco fica fora).
#
# Além das frações, vai a LINHA DO TEMPO: em que segundos havia alguém e em
# quais não havia. É o que permite ao backend, um dia, dizer "o posto ficou
# vazio das 9h12 às 9h15" em vez de carimbar o minuto inteiro.
#
# ⚠️ NÃO decide nada aqui. A borda MEDE e envia; quem classifica é o backend.
# Uma borda que decidisse `posto_vazio` sozinha só trocaria o lugar do erro.
PRESENCA_PERFIL   = _env("PRESENCA_PERFIL", "true").lower() == "true"
# O passo do perfil é o do pontuador (SELECAO_AMOSTRA_S) — a medição vem da
# MESMA decodificação e da MESMA inferência, então não existe um segundo passo
# a configurar. Um knob próprio aqui só criaria a ilusão de que dá para adensar
# a linha do tempo sem pagar YOLO de novo.
# Teto de janelas enviadas. Um segmento picotado geraria centenas de intervalos
# e um payload que não cabe num campo de formulário; acima disto só as frações
# e a maior ausência viajam, e o log diz que truncou.
PRESENCA_MAX_JANELAS = int(_env("PRESENCA_MAX_JANELAS", "60"))
# Guarda o perfil ao lado do segmento (.presenca.json). O backend ainda não lê
# o sinal; sem o arquivo, a medição do dia de hoje se perderia.
PRESENCA_SIDECAR  = _env("PRESENCA_SIDECAR", "true").lower() == "true"

# ── RECORTE ROI: compressão p/ caber no teto do Storage (Fase 38) ───────────
# O Storage do Supabase no plano FREE rejeita arquivos > 50MB (HTTP 413). O
# recorte ROI de segmentos longos estourava (30min @ crf 23 ≈ 68MB). Estes
# knobs comprimem o recorte (a plataforma reduz p/ ≤1024px de qualquer jeito e
# amostra 1 frame/5s — 15fps/crf 28 é folgado p/ a análise). Se ainda passar
# do teto, um re-encode de resgate e, no limite, erro claro pedindo segmentos
# menores (SEGMENTO_MIN=10 → recorte ≈ 20MB, cabe sem apertar).
RECORTE_CRF    = int(_env("RECORTE_CRF", "28"))     # 23=original, 28≈½ tamanho
RECORTE_FPS    = float(_env("RECORTE_FPS", "15"))   # 0/≤0 = mantém fps original
RECORTE_MAX_MB = float(_env("RECORTE_MAX_MB", "48"))  # margem do teto de 50MB
# Fase 43: segmento menor que isso = truncado/corrompido (stall de câmera) —
# nem tenta re-encodar (o ffmpeg gasta minutos e devolve saída vazia).
RECORTE_MIN_KB = float(_env("RECORTE_MIN_KB", "64"))
# Fase 43: roda o ffmpeg do recorte com prioridade baixa (nice) + poucos
# threads p/ não starvar as CAPTURAS na CPU do Pi durante o "processar durante".
RECORTE_NICE   = _env("RECORTE_NICE", "on").lower() in ("1", "on", "true", "sim")

# ── SELEÇÃO TOP-K POR HORA (--processar, Fase 22) ───────────────────────────
# Pareto de segmentos: em vez de subir TODO segmento com gente, sobe só a
# fração SELECAO_QUOTA mais "ativa" de cada hora (pontuação = presença de
# pessoa + movimento no ROI), decidida por PAR cam1+cam2 (mesmo slot de
# relógio) para não quebrar o dual-angle. Uma amostra de CALIBRAÇÃO mediana
# por hora mantém o desperdício visível nas métricas (sem ela, jogar fora os
# segmentos parados infla o "produtivo" do dashboard).
#   SELECAO_QUOTA=1.0  → DESLIGADO (comportamento atual, porteiro binário).
#   Piloto recomendado: SELECAO_QUOTA=0.4 no .env (≈3 de 6 pares/hora).
# Exige FILTRAR_PRESENCA=true (a pontuação subsume o porteiro).
SELECAO_QUOTA               = float(_env("SELECAO_QUOTA", "1.0"))
SELECAO_MIN_POR_HORA        = int(_env("SELECAO_MIN_POR_HORA", "1"))    # piso de pares/hora
SELECAO_CALIBRACAO_POR_HORA = int(_env("SELECAO_CALIBRACAO_POR_HORA", "1"))  # 0 desliga a mediana
SELECAO_PESO_MOVIMENTO      = float(_env("SELECAO_PESO_MOVIMENTO", "0.3"))   # 0..1 (resto = presença)
SELECAO_MOV_REF             = float(_env("SELECAO_MOV_REF", "10.0"))    # absdiff médio "alto" (normaliza)
SELECAO_AMOSTRA_S           = float(_env("SELECAO_AMOSTRA_S", "6.0"))   # passo do pontuador (s)
SELECAO_DESCARTE            = _env("SELECAO_DESCARTE", "apagar")        # apagar | manter

# ── ZONAS DO POSTO (--processar, Fase 28) ───────────────────────────────────
# O score/porteiro passam a contar SÓ presença/movimento dentro dos polígonos
# desenhados na plataforma (Configurações → Zonas): posto do operador +
# interação. Transeuntes passando na frente do torno deixam de inflar o score
# e de puxar segmento pro top-K. As zonas são baixadas do backend
# (GET /processos/{id}/zonas) e cacheadas em SAIDA_DIR/zonas_cache.json;
# sem rede usa o cache (idade < ZONAS_TTL_H); sem cache → comportamento
# anterior (ROI retangular). Coordenadas das zonas = espaço do VÍDEO ENVIADO
# (o recorte CAMn_ROI) — conversão p/ quadro cheio é automática.
ZONAS_REMOTAS = _env("ZONAS_REMOTAS", "true").lower() == "true"
ZONAS_TTL_H   = float(_env("ZONAS_TTL_H", "12"))
ZONAS_LOG     = _env("ZONAS_LOG", "false").lower() == "true"

# ── PROCESSAMENTO DURANTE A CAPTURA (--capturar, Fase 37) ───────────────────
# O dia inteiro de gravação bruta (~3-4GB/h com 2 câmeras em main-stream) NÃO
# cabe no cartão do Pi. Com PROCESSAR_DURANTE=true, o próprio --capturar roda
# o ciclo de seleção a cada PROCESSAR_INTERVALO_MIN sobre as HORAS JÁ
# FECHADAS: pontua, sobe o top-K e APAGA tudo da hora — o disco fica em
# regime constante de ~1h de vídeo, e a plataforma recebe os dados ao longo
# do dia (não só de madrugada).
#   SEGMENTO_FECHADO_S: nunca tocar em arquivo modificado há menos que isso
#   (é o segmento que o ffmpeg AINDA grava) — mata o "moov atom not found".
#   DISCO_MIN_LIVRE_GB: abaixo disso, alerta claro no log (retenção acumulando).
PROCESSAR_DURANTE       = _env("PROCESSAR_DURANTE", "false").lower() == "true"
PROCESSAR_INTERVALO_MIN = float(_env("PROCESSAR_INTERVALO_MIN", "60"))
SEGMENTO_FECHADO_S      = float(_env("SEGMENTO_FECHADO_S", "90"))
DISCO_MIN_LIVRE_GB      = float(_env("DISCO_MIN_LIVRE_GB", "2.0"))


def _env_int(nome, padrao):
    """int() tolerante: valor lixo NÃO derruba o import (senão `--help` e
    `--turno-info` morrem antes de conseguirem explicar o erro). Devolve
    (valor, ok) — quem precisa de validação dura chama o validador em main()."""
    bruto = _env(nome, str(padrao))
    try:
        return int(str(bruto).strip()), True
    except (TypeError, ValueError):
        return int(padrao), False


# ── TURNO POR RELÓGIO DE PAREDE (--capturar, Fase 51) ───────────────────────
# PROBLEMA que isto resolve: `--capturar` gravava JANELA_CAPTURA_H horas A PARTIR
# DO INSTANTE da chamada. Se o systemd disparasse atrasado (boot lento, queda de
# energia, timer perdido), a janela "escorregava" e invadia o almoço ou passava
# do fim do expediente. Numa campanha de 30 dias sem ninguém olhando, isso
# contamina a medição e gasta cartão à toa.
#
# SOLUÇÃO: o fim da captura passa a ser um INSTANTE ABSOLUTO de relógio (o fim da
# janela do turno), não uma duração. Subiu 09:12 numa janela 06:00–11:30? Grava
# 2h18 e encerra 11:30 em ponto. Uma invocação = UMA janela; a segunda janela do
# dia é outro timer.
#
# TURNO_JANELAS vazio = COMPORTAMENTO ATUAL INALTERADO (retrocompatibilidade).
TURNO_JANELAS        = (_env("TURNO_JANELAS", "") or "").strip()
TURNO_DIAS           = (_env("TURNO_DIAS", "1-5") or "").strip()
TURNO_FERIADOS       = (_env("TURNO_FERIADOS", "") or "").strip()
TURNO_TOLERANCIA_MIN = float(_env("TURNO_TOLERANCIA_MIN", "5"))

# ── AMOSTRAGEM SISTEMÁTICA (--processar, Fase 51) ───────────────────────────
# PORQUÊ (viés de medição — leia antes de mexer): o top-K (SELECAO_QUOTA) escolhe
# os segmentos de MAIOR score de atividade. Isso é ótimo para "achar o evento
# interessante", mas é ESTATISTICAMENTE INVÁLIDO para responder "% do tempo
# produtivo": ao preferir os trechos mais movimentados, a amostra deixa de ser
# representativa e a produtividade medida sai INFLADA. A campanha de 30 dias
# existe justamente para medir esse número — então a seleção precisa ser
# independente do conteúdo.
#
# A amostragem SISTEMÁTICA resolve: escolhe os slots por uma regra puramente
# TEMPORAL (a cada N minutos, pegue o slot de índice FASE), sem olhar o vídeo.
# Todo instante do turno tem a mesma probabilidade de entrar na amostra, e a
# cobertura fica distribuída ao longo da hora — em vez de um bloco contínuo de
# 30 min que perderia eventos concentrados fora dele. Com SEGMENTO_MIN=5,
# PERIODO=10, FASE=0: grava :00,:10,:20,:30,:40,:50 e pula :05,:15,...
#
# Bônus: é temporal, então cam1 e cam2 selecionam EXATAMENTE os mesmos slots
# sozinhas — o pareamento dual-angle continua íntegro sem nenhuma coordenação.
AMOSTRAGEM_MODO              = (_env("AMOSTRAGEM_MODO", "off") or "off").strip().lower()
AMOSTRAGEM_PERIODO_MIN, _AM_PER_OK  = _env_int("AMOSTRAGEM_PERIODO_MIN", 10)
AMOSTRAGEM_FASE, _AM_FASE_OK        = _env_int("AMOSTRAGEM_FASE", 0)
# Sufixo dos segmentos FORA da amostra. Descarte NÃO-DESTRUTIVO: o segmento fica
# no disco como rede de segurança (auditar uma zona cinza depois) e some sozinho
# pela retenção normal (limpar_antigos → RETENCAO_DIAS / MIN_GB_LIVRE).
SUFIXO_SKIP = "_skip.mp4"

# ── HEARTBEAT / SAÚDE DA BORDA (Fase 52) ────────────────────────────────────
# O Pi roda sozinho na fábrica por 30 dias. Sem pulso, uma câmera caída, um
# ffmpeg morto ou um cartão cheio só aparecem DIAS depois como buraco no
# dashboard (já aconteceu: um dia inteiro sem gravação, notado numa reunião).
#
# ⚠️ REGRA INVIOLÁVEL: o heartbeat é FIRE-AND-FORGET. Timeout curto, exceção
# engolida e logada. Ele NUNCA pode atrasar, bloquear ou derrubar uma captura.
# Backend fora do ar = a gravação continua exatamente igual. Esta regra vence
# todas as outras deste bloco.
HEARTBEAT_ENABLE        = _env("HEARTBEAT_ENABLE", "true").lower() == "true"
HEARTBEAT_INTERVALO_MIN = float(_env("HEARTBEAT_INTERVALO_MIN", "5"))
HEARTBEAT_TIMEOUT_S     = float(_env("HEARTBEAT_TIMEOUT_S", "5"))   # curto de propósito
RUNNER_VERSAO           = "52"


# ── VALIDAÇÃO PRECOCE de placeholder (igual ao Conexão_RTSP.ipynb) ────────────
def _validar_rtsp(url, nome):
    placeholders = ("USUARIO", "IP_DA_CAMERA")
    achados = [p for p in placeholders if p in url]
    if achados:
        raise ValueError(
            f"{nome} ainda contém placeholder(s) {achados}. Preencha a URL RTSP real "
            f"no .env (ou inline) antes de rodar. Valor atual: {url}"
        )


def _mascarar(url):
    """Esconde a senha da URL ao logar."""
    try:
        import re
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)
    except Exception:
        return url


def _avisar_url_suspeita(url):
    """Detecção (não reescreve): senha com '@' não-escapado quebra o ffmpeg.
    Mais de um '@' na URL = caractere reservado sem percent-encode."""
    if url.count("@") > 1:
        log.warning("URL RTSP tem mais de um '@' — a senha provavelmente precisa de "
                    "percent-encode (ex.: '@' → '%%40'), senão o ffmpeg trava. Veja o README.")


# ─────────────────────────────────────────────────────────────────────────────
# CÂMERAS — auto-descoberta no .env (CAM1_, CAM2_, ...) com fallback p/ 1 câmera.
# Adicionar câmera = copiar o bloco CAMn_ e mudar o número, sem tocar no código.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Camera:
    id: str            # "cam1", "cam2"
    nome: str          # CAMn_NOME ou o id
    rtsp_main: str
    rtsp_sub: str
    processo_id: str   # pode repetir entre câmeras (mesmo posto) ou ser distinto
    roi: tuple | None = None   # ROI normalizada (x, y, w, h) em [0–1]; None = quadro inteiro

    @property
    def url(self) -> str:
        return self.rtsp_sub if USAR_SUBSTREAM else self.rtsp_main

    @property
    def seg_dir(self) -> Path:
        return SEG_DIR / self.id


def _parse_roi(valor):
    '''ROI "x,y,w,h" normalizada [0–1] (canto sup-esq + largura/altura) → tupla de
    floats. Vazio/ausente/inválida → None (analisa o quadro inteiro).'''
    if not valor:
        return None
    try:
        x, y, w, h = (float(p) for p in valor.replace(" ", "").split(","))
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1
                and x + w <= 1.001 and y + h <= 1.001):
            raise ValueError("fora de [0–1] ou ultrapassa a borda")
        return (x, y, w, h)
    except Exception as e:
        log.warning("ROI inválida (%r): %s — usando o quadro inteiro. Formato: x,y,w,h em [0–1].",
                    valor, e)
        return None


def carregar_cameras() -> list:
    '''Varre CAM1_, CAM2_, ... no .env e para no PRIMEIRO número ausente. Sem nenhum
    CAMn_, cai no fallback de 1 câmera (RTSP_URL_* + PROCESSO_ID, id "cam1"). Valida
    cada URL (_validar_rtsp/_avisar_url_suspeita) e loga a lista (senha mascarada).'''
    cams = []
    n = 1
    while True:
        sub  = _env(f"CAM{n}_RTSP_SUB")
        main = _env(f"CAM{n}_RTSP_MAIN")
        if sub is None and main is None:
            break   # primeiro número ausente → fim da auto-descoberta
        cams.append(Camera(
            id=f"cam{n}",
            nome=_env(f"CAM{n}_NOME", f"cam{n}"),
            rtsp_main=main or sub,        # se só um stream existir, usa-o nos dois
            rtsp_sub=sub or main,
            processo_id=_env(f"CAM{n}_PROCESSO_ID", PROCESSO_ID),
            roi=_parse_roi(_env(f"CAM{n}_ROI")),
        ))
        n += 1

    if not cams:   # Fallback: 1 câmera legada (RTSP_URL_* + PROCESSO_ID)
        cams.append(Camera(id="cam1", nome=_env("CAM1_NOME", "cam1"),
                            rtsp_main=RTSP_URL_MAIN, rtsp_sub=RTSP_URL_SUB,
                            processo_id=PROCESSO_ID,
                            roi=_parse_roi(_env("CAM1_ROI"))))

    for cam in cams:
        _validar_rtsp(cam.url, f"{cam.id} ({cam.nome})")
        _avisar_url_suspeita(cam.url)
        roi_txt = (",".join(f"{v:.3f}" for v in cam.roi)) if cam.roi else "quadro inteiro"
        log.info("Câmera %s [%s] · proc=%s · %s · ROI=%s",
                 cam.id, cam.nome, cam.processo_id, _mascarar(cam.url), roi_txt)
    return cams


# ─────────────────────────────────────────────────────────────────────────────
# Helpers REAPROVEITADOS (Célula 4) — copiados de backend/pipeline.py.
# MANTER IDÊNTICO ao da nuvem (consistência borda↔nuvem).
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Amostra:
    '''Um instante amostrado: frame + lista de pessoas visíveis.'''
    frame_idx: int
    tempo_s: float
    frame_bgr: np.ndarray
    pessoas: list  # lista de dicts {'track_id', 'bbox', 'centro', 'zona'}


def anotar_frame_com_ids(frame_bgr, pessoas):
    '''Desenha P1, P2... sobre cada pessoa. O VLM usa esses marcadores para
    ancorar suas descrições ao track_id real do BoT-SORT.

    pessoas: lista de dicts {'track_id': int, 'bbox': (x1,y1,x2,y2), 'rotulo': 'P1'}
    Retorna o frame anotado.
    '''
    f = frame_bgr.copy()
    for p in pessoas:
        x1, y1, x2, y2 = p['bbox']
        cor = (0, 255, 100)  # verde vivo
        # Bbox
        cv2.rectangle(f, (x1, y1), (x2, y2), cor, 3)
        # Label P1/P2 com fundo
        label = p['rotulo']
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 1.4, 3)
        cv2.rectangle(f, (x1, y1 - th - 14), (x1 + tw + 14, y1), cor, -1)
        cv2.putText(f, label, (x1 + 6, y1 - 6), cv2.FONT_HERSHEY_DUPLEX,
                    1.4, (0, 0, 0), 3)
    return f


def frame_para_base64(frame_bgr, max_lado=1024, qualidade=85):
    '''Converte frame BGR para data-URL base64 JPEG. Redimensiona se grande
    (Groq aceita imagens grandes mas processar pixels demais é desperdício).'''
    h, w = frame_bgr.shape[:2]
    if max(h, w) > max_lado:
        escala = max_lado / max(h, w)
        frame_bgr = cv2.resize(frame_bgr, (int(w * escala), int(h * escala)))
    ok, buf = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
    assert ok
    return base64.b64encode(buf.tobytes()).decode('ascii')


# ─────────────────────────────────────────────────────────────────────────────
# RTSP robusto (Célula 5) — copiado verbatim.
# ─────────────────────────────────────────────────────────────────────────────

def abrir_rtsp(url, timeout_ms=TIMEOUT_RTSP_MS):
    '''Abre o stream via FFmpeg forçando TCP (env OPENCV_FFMPEG_CAPTURE_OPTIONS) +
    timeouts de abertura/leitura + buffer mínimo. Não levanta — retorna o cap.'''
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    for prop_nome, valor in (("CAP_PROP_OPEN_TIMEOUT_MSEC", timeout_ms),
                             ("CAP_PROP_READ_TIMEOUT_MSEC", timeout_ms),
                             ("CAP_PROP_BUFFERSIZE", 1)):
        prop = getattr(cv2, prop_nome, None)
        if prop is not None:
            try:
                cap.set(prop, valor)
            except Exception:
                pass
    return cap


def escolher_url():
    '''Retorna a URL do sub-stream (default, leve no Pi) ou do main, conforme
    USAR_SUBSTREAM.'''
    url = RTSP_URL_SUB if USAR_SUBSTREAM else RTSP_URL_MAIN
    log.info("URL RTSP (%s-stream): %s", "sub" if USAR_SUBSTREAM else "main", _mascarar(url))
    return url


def ler_frame_resiliente(cap, url, estado, max_falhas=MAX_FALHAS_RECONEXAO):
    '''Lê 1 frame. Em falhas consecutivas, conta; ao atingir `max_falhas`, reabre
    o stream com backoff exponencial (até BACKOFF_MAX_S) — healthcheck.

    `estado` é um dict mutável {'falhas': int, 'backoff': float}. RETORNA
    (ok, frame, cap) — o chamador DEVE atualizar seu handle `cap`, que pode ter
    sido reaberto.
    '''
    ok, frame = (cap.read() if (cap is not None and cap.isOpened()) else (False, None))
    if ok and frame is not None:
        estado["falhas"] = 0
        estado["backoff"] = 1.0
        return True, frame, cap

    estado["falhas"] = estado.get("falhas", 0) + 1
    log.warning("Falha de leitura RTSP (%d/%d)", estado["falhas"], max_falhas)
    if estado["falhas"] >= max_falhas:
        espera = min(estado.get("backoff", 1.0), BACKOFF_MAX_S)
        log.warning("Healthcheck: reabrindo stream em %.1fs ...", espera)
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass
        time.sleep(espera)
        cap = abrir_rtsp(url)
        estado["falhas"] = 0
        estado["backoff"] = min(estado.get("backoff", 1.0) * 2, BACKOFF_MAX_S)
    return False, None, cap


def fps_seguro(cap, fallback=12.0):
    '''FPS do stream; RTSP às vezes devolve 0/absurdo — cai no fallback.'''
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
    except Exception:
        fps = 0.0
    return fps if 1.0 <= fps <= 120.0 else fallback


# ─────────────────────────────────────────────────────────────────────────────
# Estado de parada limpa (SIGINT/SIGTERM sob systemd)
# ─────────────────────────────────────────────────────────────────────────────
_parar = False
_procs_ffmpeg = []    # processos ffmpeg em andamento (p/ terminar TODOS no sinal)


def _handler_sinal(signum, _frame):
    global _parar
    _parar = True
    log.info("Sinal %s recebido — encerrando de forma limpa após o ciclo atual...", signum)
    for p in list(_procs_ffmpeg):
        try:
            p.terminate()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Bloco 1 → subcomando --check
# ─────────────────────────────────────────────────────────────────────────────
def checar_conexao(cam) -> bool:
    '''Abre o RTSP da câmera, lê 1 frame, loga resolução/FPS e salva
    edge_test_frame_<id>.jpg.'''
    log.info("[%s] Abrindo stream para teste: %s", cam.id, _mascarar(cam.url))
    cap = abrir_rtsp(cam.url)
    if not cap.isOpened():
        log.error("[%s] Não abriu o stream. Cheque rede/IP, usuário/senha, canal RTSP "
                  "e a env TCP.", cam.id)
        return False
    try:
        ok, frame = False, None
        for _ in range(30):   # algumas câmeras demoram a entregar o 1º frame
            ok, frame = cap.read()
            if ok and frame is not None:
                break
            time.sleep(0.2)
        if not ok or frame is None:
            log.error("[%s] Stream abriu mas não veio frame (tente o main-stream / "
                      "USAR_SUBSTREAM=False).", cam.id)
            return False
        h, w = frame.shape[:2]
        fps = fps_seguro(cap)
        log.info("[%s] Conexão OK · resolução %dx%d · ~%.1f FPS", cam.id, w, h, fps)
        destino = SAIDA_DIR / f"edge_test_frame_{cam.id}.jpg"
        cv2.imwrite(str(destino), frame)
        log.info("[%s] Frame salvo em %s", cam.id, destino.resolve())
        if cam.roi is not None:   # preview da ROI: retângulo vermelho p/ conferir/ajustar
            rx, ry, rw, rh = cam.roi
            prev = frame.copy()
            cv2.rectangle(prev, (int(rx * w), int(ry * h)),
                          (int((rx + rw) * w), int((ry + rh) * h)), (0, 0, 255), 3)
            alvo_prev = SAIDA_DIR / f"edge_test_roi_{cam.id}.jpg"
            cv2.imwrite(str(alvo_prev), prev)
            log.info("[%s] ROI %s → confira o retângulo em %s e ajuste o .env se preciso.",
                     cam.id, ",".join(f"{v:.3f}" for v in cam.roi), alvo_prev.name)
        return True
    finally:
        cap.release()


# ─────────────────────────────────────────────────────────────────────────────
# MODO A — gravar clipe (Bloco 4) + upload/poll (Bloco 5)
# ─────────────────────────────────────────────────────────────────────────────
def gravar_clipe(cam):
    '''Grava um clipe da câmera via ffmpeg (-c:v copy, sem reencode). Path ou None.
    Nome inclui o id da câmera; usa cam.url; registra o proc em _procs_ffmpeg.'''
    if shutil.which("ffmpeg") is None:
        log.error("ffmpeg não encontrado no PATH. Instale: sudo apt-get install -y ffmpeg")
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    clip = SAIDA_DIR / f"edge_clip_{cam.id}_{ts}.mp4"
    cmd = ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", cam.url,
           "-t", str(DURACAO_CLIPE_S), "-an", "-c:v", "copy", str(clip)]
    log.info("[%s] Gravando clipe de %ds → %s", cam.id, DURACAO_CLIPE_S, clip.name)
    log.info("[%s] cmd: ffmpeg -y -rtsp_transport tcp -i <url> -t %d -an -c:v copy %s",
             cam.id, DURACAO_CLIPE_S, clip.name)   # não loga a url (tem senha)

    # Popen (em vez de run) para podermos terminar no SIGTERM do systemd.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _procs_ffmpeg.append(proc)
    try:
        try:
            _, err = proc.communicate(timeout=DURACAO_CLIPE_S + 60)
            rc = proc.returncode
            if rc != 0:
                log.error("[%s] ffmpeg falhou (rc=%d). Últimas linhas:\n%s",
                          cam.id, rc, "\n".join((err or "").strip().splitlines()[-8:]))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            log.error("[%s] ffmpeg estourou o timeout — câmera lenta ou stream caiu.", cam.id)
    finally:
        try:
            _procs_ffmpeg.remove(proc)
        except ValueError:
            pass

    if clip.exists() and clip.stat().st_size > 0:
        log.info("[%s] Clipe OK · %.1f MB · %s", cam.id, clip.stat().st_size / 1e6, clip.resolve())
        return clip
    log.error("[%s] Clipe não foi gerado (0 bytes/ausente).", cam.id)
    return None


# ── Sessão Supabase: login ÚNICO com renovação (em vez de login por segmento) ──
_sessao = {"headers": None, "ts": 0.0}


def headers_auth(forcar=False) -> dict:
    '''sign_in_with_password 1x, cacheia o Bearer e renova quando passar ~50 min
    (token Supabase dura ~1h) ou quando forçado (ex.: após um 401). Um login serve
    para muitos uploads — evita estourar o limite de auth no meio do lote.'''
    if forcar or _sessao["headers"] is None or (time.monotonic() - _sessao["ts"]) > 3000:
        from supabase import create_client   # import sob demanda
        sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        auth = sb.auth.sign_in_with_password({"email": EMAIL, "password": SENHA})
        _sessao["headers"] = {"Authorization": f"Bearer {auth.session.access_token}"}
        _sessao["ts"] = time.monotonic()
        log.info("Sessão Supabase renovada (login como %s).", EMAIL)
    return _sessao["headers"]


# ── Zonas do posto (Fase 28): download + cache + transformação ───────────────
_ZONAS_CACHE_PATH = SAIDA_DIR / "zonas_cache.json"


def baixar_zonas(cameras) -> dict:
    '''Baixa as zonas ATIVAS de cada processo das câmeras:
      {processo_id: {cam_id: [ {nome, papel, pts_rel}, ... ]}}
    Sucesso → grava o cache. Falha de rede → cache se idade < ZONAS_TTL_H;
    sem cache → {} (fallback: ROI retangular, comportamento antigo).'''
    procs = sorted({c.processo_id for c in cameras})
    zonas: dict = {}
    try:
        headers = headers_auth()
        for pid in procs:
            r = requests.get(f"{API_URL}/processos/{pid}/zonas", headers=headers, timeout=30)
            r.raise_for_status()
            por_cam: dict = {}
            for z in r.json() or []:
                if not z.get("ativo") or len(z.get("pts_rel") or []) < 3:
                    continue
                por_cam.setdefault(z.get("cam_id"), []).append(
                    {"nome": z.get("nome"), "papel": z.get("papel"),
                     "pts_rel": z["pts_rel"]}
                )
            zonas[pid] = por_cam
        try:
            SAIDA_DIR.mkdir(parents=True, exist_ok=True)
            _ZONAS_CACHE_PATH.write_text(
                json.dumps({"ts": time.time(), "zonas": zonas}), encoding="utf-8"
            )
        except Exception as e:
            log.warning("Cache de zonas não gravado (%s) — segue em memória.", e)
        n = sum(len(v) for pc in zonas.values() for v in pc.values())
        log.info("Zonas do posto: %d zona(s) baixadas de %d processo(s).", n, len(procs))
        return zonas
    except Exception as e:
        try:
            dados = json.loads(_ZONAS_CACHE_PATH.read_text(encoding="utf-8"))
            idade_h = (time.time() - float(dados.get("ts", 0))) / 3600.0
            if idade_h <= ZONAS_TTL_H:
                log.warning("Zonas: rede falhou (%s) — usando CACHE de %.1fh.", e, idade_h)
                return dados.get("zonas", {})
            log.warning("Zonas: rede falhou e cache velho (%.1fh > %.0fh) — sem zonas.",
                        idade_h, ZONAS_TTL_H)
        except Exception:
            log.warning("Zonas: rede falhou (%s) e sem cache — modo ROI retangular.", e)
        return {}


def _zonas_da_cam(zonas: dict, cam) -> list | None:
    '''pts_rel dos polígonos que interessam ao score (posto_operador ∪
    interacao) desta câmera. None quando NÃO há posto_operador — aí o
    porteiro/pontuador seguem no comportamento antigo (ROI retangular).'''
    lst = (zonas.get(cam.processo_id) or {}).get(cam.id) or []
    if not any(z.get("papel") == "posto_operador" for z in lst):
        return None
    return [z["pts_rel"] for z in lst if z.get("papel") in ("posto_operador", "interacao")]


def _zonas_polys_px(zonas_pts, roi, usa_crop, alvo_shape, frame_shape) -> list:
    '''Converte as zonas (espaço do VÍDEO ENVIADO = recorte da ROI) para
    polígonos em PIXELS do alvo do YOLO:
      • usa_crop=True  → o alvo JÁ É o recorte → escala direta pelo alvo;
      • usa_crop=False → converte recorte→quadro cheio (x_full = roi.x + x·roi.w)
        e escala pelo frame; sem roi, zona ≡ quadro cheio.'''
    polys = []
    ah, aw = alvo_shape[:2]
    hh, ww = frame_shape[:2]
    for pts in zonas_pts:
        if usa_crop or roi is None:
            poly = [[int(x * aw), int(y * ah)] for x, y in pts]
        else:
            rx, ry, rw, rh = roi
            poly = [[int((rx + x * rw) * ww), int((ry + y * rh) * hh)] for x, y in pts]
        polys.append(np.array(poly, dtype=np.int32))
    return polys


def _toca_poligono(boxes, kpts_list, polys, alvo_shape) -> "np.ndarray":
    '''Fase 31: a pessoa conta como "na zona" se QUALQUER parte do corpo tocar
    algum polígono — os 17 keypoints do yolo11n-pose (punhos, tornozelos,
    joelhos...) desnormalizados pro alvo + a âncora topo-do-tronco
    (cx, y1+0.30·h) como garantia p/ pose parcial/ausente. Um pé ou um braço
    dentro da zona já aprova a detecção.'''
    ah, aw = alvo_shape[:2]
    out = np.zeros(len(boxes), dtype=bool)
    for i, b in enumerate(boxes):
        pontos = []
        if kpts_list is not None and i < len(kpts_list):
            for k in kpts_list[i]:
                if k[0] > 0 and k[1] > 0:
                    pontos.append((float(k[0]) * aw, float(k[1]) * ah))
        pontos.append((float((b[0] + b[2]) / 2.0),
                       float(b[1] + 0.30 * max(0.0, b[3] - b[1]))))
        for poly in polys:
            if any(cv2.pointPolygonTest(poly, p, False) >= 0 for p in pontos):
                out[i] = True
                break
    return out


def _ancora_na_zona(boxes, kpts_list, polys, alvo_shape) -> "np.ndarray":
    '''A regra ESTRITA — a MESMA que a nuvem usa para decidir quem está no
    posto: só conta se a ÂNCORA cair dentro do polígono.

    Âncora = ponto médio dos OMBROS (kpts COCO 5 e 6); com um ombro só, ele;
    sem ombros, o NARIZ deslocado 10% da altura do bbox; sem pose nenhuma,
    topo-do-tronco do bbox (cx, y1 + 0.30·h). Ela diz ONDE A PESSOA ESTÁ, não
    até onde ela ALCANÇA — por isso sobrevive à oclusão pela máquina sem contar
    quem só estica um braço para dentro da zona.

    Existe ao lado de `_toca_poligono` de propósito: a DIFERENÇA entre as duas
    é o sinal de "a pessoa trabalha na borda do polígono", e é isso que explica
    metade dos `posto_vazio` errados.'''
    ah, aw = alvo_shape[:2]
    out = np.zeros(len(boxes), dtype=bool)
    for i, b in enumerate(boxes):
        alvo = None
        k = kpts_list[i] if (kpts_list is not None and i < len(kpts_list)) else None
        if k is not None and len(k) >= 7:
            oe, od = k[5], k[6]
            ve, vd = (oe[0] > 0 and oe[1] > 0), (od[0] > 0 and od[1] > 0)
            if ve and vd:
                alvo = ((oe[0] + od[0]) / 2 * aw, (oe[1] + od[1]) / 2 * ah)
            elif ve:
                alvo = (float(oe[0]) * aw, float(oe[1]) * ah)
            elif vd:
                alvo = (float(od[0]) * aw, float(od[1]) * ah)
            elif k[0][0] > 0 and k[0][1] > 0:
                alvo = (float(k[0][0]) * aw,
                        float(k[0][1]) * ah + 0.10 * max(0.0, b[3] - b[1]))
        if alvo is None:
            alvo = (float((b[0] + b[2]) / 2.0),
                    float(b[1] + 0.30 * max(0.0, b[3] - b[1])))
        for poly in polys:
            if cv2.pointPolygonTest(poly, alvo, False) >= 0:
                out[i] = True
                break
    return out


def _janelas_de(marcas: list, passo_s: float, teto: int) -> list:
    '''Amostras booleanas em ordem → intervalos [ini_s, fim_s] em que houve
    alguém. Sem inventar nada entre amostras: o intervalo vai do instante da
    primeira amostra positiva ao FIM do passo da última.'''
    janelas = []
    inicio = None
    for i, v in enumerate(marcas):
        if v and inicio is None:
            inicio = i
        elif not v and inicio is not None:
            janelas.append([round(inicio * passo_s, 1), round(i * passo_s, 1)])
            inicio = None
    if inicio is not None:
        janelas.append([round(inicio * passo_s, 1),
                        round(len(marcas) * passo_s, 1)])
    return janelas[:max(0, teto)]


def _maior_ausencia(marcas: list, passo_s: float) -> float:
    '''A maior sequência de amostras SEM ninguém, em segundos. É o número que
    separa "saiu para pegar uma chave" de "o posto ficou vazio".'''
    maior = atual = 0
    for v in marcas:
        atual = 0 if v else atual + 1
        maior = max(maior, atual)
    return round(maior * passo_s, 1)


def _kpts_do_predict(res):
    '''keypoints.xyn do resultado do YOLO predict (ou None).'''
    try:
        if getattr(res[0], "keypoints", None) is not None and res[0].keypoints.xyn is not None:
            return res[0].keypoints.xyn.cpu().numpy()
    except Exception:
        pass
    return None


_RE_SEG_TS = __import__("re").compile(r"seg_(\d{8})_(\d{6})")


# ═════════════════════════════════════════════════════════════════════════════
# TURNO POR RELÓGIO DE PAREDE (Fase 51) — funções PURAS
#
# Tudo aqui recebe o instante como PARÂMETRO (nunca chama datetime.now() por
# dentro). Isso não é preciosismo: é o que torna possível testar "sábado",
# "feriado", "almoço" e "janela que termina 15:48" sem esperar o relógio, e é o
# que me deixa validar a config da campanha hoje à tarde via --turno-info.
# ═════════════════════════════════════════════════════════════════════════════
DOW_PT = {1: "seg", 2: "ter", 3: "qua", 4: "qui", 5: "sex", 6: "sáb", 7: "dom"}


class ConfigTurnoInvalida(ValueError):
    """Configuração de turno impossível de interpretar — erro do operador, não
    do código. Sobe com mensagem pronta para o log/journalctl."""


def parse_janelas(texto: str) -> list:
    '''"06:00-11:30,12:30-15:48" → [(time(6,0), time(11,30)), (time(12,30), time(15,48))].
    Vazio → [] (= turno desligado, comportamento legado).

    Rejeita janela com fim <= início (inclusive turno que cruza a meia-noite):
    a campanha é diurna e aceitar isso silenciosamente esconderia um erro de
    digitação (ex.: "18:00-06:00") que só apareceria como captura vazia.'''
    texto = (texto or "").strip()
    if not texto:
        return []
    janelas = []
    for bruto in texto.split(","):
        parte = bruto.strip()
        if not parte:
            continue
        if "-" not in parte:
            raise ConfigTurnoInvalida(
                f"janela {parte!r} sem '-' (use HH:MM-HH:MM, ex.: 06:00-11:30)")
        ini_txt, _, fim_txt = parte.partition("-")
        try:
            hi, mi = [int(x) for x in ini_txt.strip().split(":")]
            hf, mf = [int(x) for x in fim_txt.strip().split(":")]
            ini, fim = _dtime(hi, mi), _dtime(hf, mf)
        except (ValueError, TypeError):
            raise ConfigTurnoInvalida(
                f"janela {parte!r} inválida (use HH:MM-HH:MM em 24h, ex.: 06:00-11:30)")
        if fim <= ini:
            raise ConfigTurnoInvalida(
                f"janela {parte!r}: fim <= início. Turno que cruza a meia-noite não é "
                f"suportado — quebre em duas janelas em dias diferentes.")
        janelas.append((ini, fim))
    janelas.sort(key=lambda j: j[0])
    # Sobreposição = ambiguidade sobre qual deadline vale. Avisa alto e segue
    # (a resolução usa a PRIMEIRA que contém o instante).
    for (a_ini, a_fim), (b_ini, b_fim) in zip(janelas, janelas[1:]):
        if b_ini < a_fim:
            log.warning("Turno · janelas %s-%s e %s-%s se SOBREPÕEM — vale a primeira "
                        "que contiver o instante.", a_ini.strftime("%H:%M"),
                        a_fim.strftime("%H:%M"), b_ini.strftime("%H:%M"),
                        b_fim.strftime("%H:%M"))
    return janelas


def parse_dias(texto: str) -> set:
    '''"1-5" | "1,3,5" | "1-5,6" → {1..5} etc. ISO: 1=segunda … 7=domingo.
    Vazio → todos os dias (não travar a campanha por variável em branco).'''
    texto = (texto or "").strip()
    if not texto:
        return {1, 2, 3, 4, 5, 6, 7}
    dias = set()
    for bruto in texto.split(","):
        parte = bruto.strip()
        if not parte:
            continue
        try:
            if "-" in parte:
                a, _, b = parte.partition("-")
                ini, fim = int(a.strip()), int(b.strip())
                if ini > fim:
                    raise ValueError
                dias.update(range(ini, fim + 1))
            else:
                dias.add(int(parte))
        except ValueError:
            raise ConfigTurnoInvalida(
                f"TURNO_DIAS {parte!r} inválido (use ISO 1=seg..7=dom, ex.: '1-5' ou '1,3,5')")
    fora = {d for d in dias if d < 1 or d > 7}
    if fora:
        raise ConfigTurnoInvalida(
            f"TURNO_DIAS fora da faixa 1..7: {sorted(fora)} (1=segunda … 7=domingo)")
    return dias


def parse_feriados(texto: str) -> set:
    '''"2026-09-07,2026-10-12" → {date(2026,9,7), date(2026,10,12)}.'''
    texto = (texto or "").strip()
    if not texto:
        return set()
    datas = set()
    for bruto in texto.split(","):
        parte = bruto.strip()
        if not parte:
            continue
        try:
            datas.add(_ddate.fromisoformat(parte))
        except ValueError:
            raise ConfigTurnoInvalida(
                f"TURNO_FERIADOS {parte!r} inválido (use ISO AAAA-MM-DD, ex.: 2026-09-07)")
    return datas


@dataclass
class DecisaoTurno:
    '''O que fazer AGORA. `acao`:
       "legado"   — TURNO_JANELAS vazio → duração JANELA_CAPTURA_H (como antes)
       "capturar" — dentro de uma janela; grave até `fim` (relógio absoluto)
       "aguardar" — falta pouco (<= tolerância) para abrir; durma `espera_s`
       "sair"     — dia não útil / feriado / fora de janela → exit 0 (não é erro)
    '''
    acao: str
    motivo: str
    inicio: datetime = None
    fim: datetime = None
    espera_s: float = 0.0
    janela_idx: int = None

    @property
    def duracao_s(self) -> float:
        """Segundos de gravação desta invocação (do 'agora' resolvido até o fim
        da janela). 0 quando não há o que gravar."""
        if self.fim is None or self.inicio is None:
            return 0.0
        return max(0.0, (self.fim - self.inicio).total_seconds())


def resolver_turno(agora: datetime, janelas: list, dias: set, feriados: set,
                   tolerancia_min: float) -> DecisaoTurno:
    '''Decide o estado do turno para `agora` — FUNÇÃO PURA (nada de now()/IO).

    Ordem das checagens (importa para a mensagem que vai ao journalctl):
    turno desligado → feriado → dia não útil → dentro de janela → prestes a
    abrir (tolerância) → fora de janela.'''
    if not janelas:
        return DecisaoTurno(acao="legado", motivo="TURNO_JANELAS vazio (modo duração)")

    hoje = agora.date()
    dow = agora.isoweekday()
    if hoje in feriados:
        return DecisaoTurno(acao="sair", motivo=f"feriado ({hoje.isoformat()})")
    if dow not in dias:
        return DecisaoTurno(
            acao="sair",
            motivo=f"dia não útil ({DOW_PT.get(dow, dow)}={dow}; TURNO_DIAS não inclui)")

    absolutas = [
        (datetime.combine(hoje, ini, tzinfo=agora.tzinfo),
         datetime.combine(hoje, fim, tzinfo=agora.tzinfo))
        for ini, fim in janelas
    ]
    for idx, (ini_dt, fim_dt) in enumerate(absolutas):
        if ini_dt <= agora < fim_dt:
            return DecisaoTurno(acao="capturar", motivo="dentro da janela",
                                inicio=agora, fim=fim_dt, janela_idx=idx)

    # Ainda não abriu: só espera se estiver dentro da tolerância (senão o
    # systemd ficaria com um processo dormindo horas, segurando o lock).
    futuras = [(i, a, b) for i, (a, b) in enumerate(absolutas) if a > agora]
    if futuras:
        idx, ini_dt, fim_dt = min(futuras, key=lambda t: t[1])
        espera = (ini_dt - agora).total_seconds()
        if espera <= tolerancia_min * 60:
            return DecisaoTurno(acao="aguardar",
                                motivo=f"abre em {espera:.0f}s (tolerância {tolerancia_min:.0f}min)",
                                inicio=ini_dt, fim=fim_dt, espera_s=espera, janela_idx=idx)
        return DecisaoTurno(
            acao="sair",
            motivo=(f"fora de janela (próxima {ini_dt.strftime('%H:%M')}, "
                    f"em {espera / 60:.0f}min > tolerância {tolerancia_min:.0f}min)"))
    return DecisaoTurno(acao="sair", motivo="fora de janela (nenhuma janela restante hoje)")


def _fmt_restante(seg: float) -> str:
    """3600*5+29*60 → '5h29m'. Só para o log de partida ficar legível."""
    seg = max(0, int(seg))
    h, m = seg // 3600, (seg % 3600) // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


# ═════════════════════════════════════════════════════════════════════════════
# AMOSTRAGEM SISTEMÁTICA (Fase 51) — funções PURAS
# ═════════════════════════════════════════════════════════════════════════════
def slots_por_periodo(segmento_min: int, periodo_min: int) -> int:
    """Quantos segmentos cabem num período (ex.: 10/5 = 2 → 1 gravado, 1 pulado)."""
    return int(periodo_min) // int(segmento_min)


def validar_amostragem(segmento_min: int, periodo_min: int, fase: int) -> str:
    '''Devolve "" se a config é válida, ou a mensagem de erro pronta.
    Erro aqui é FATAL (exit 2): rodar 30 dias com amostragem inválida seria
    coletar lixo silenciosamente.'''
    if segmento_min <= 0:
        return f"SEGMENTO_MIN={segmento_min} inválido (precisa ser > 0)."
    if periodo_min <= 0:
        return f"AMOSTRAGEM_PERIODO_MIN={periodo_min} inválido (precisa ser > 0)."
    if periodo_min % segmento_min != 0:
        return (f"AMOSTRAGEM_PERIODO_MIN={periodo_min} não é múltiplo de "
                f"SEGMENTO_MIN={segmento_min}. Ex.: SEGMENTO_MIN=5 → período 5, 10, 15, 20...")
    n = slots_por_periodo(segmento_min, periodo_min)
    if not (0 <= fase < n):
        return (f"AMOSTRAGEM_FASE={fase} fora da faixa: com SEGMENTO_MIN={segmento_min} e "
                f"PERIODO={periodo_min} existem {n} slot(s), então a fase vai de 0 a {n - 1}.")
    return ""


def indice_slot(minuto: int, segmento_min: int, periodo_min: int) -> int:
    """Índice do segmento dentro do período. `minuto` = MINUTO DA HORA (0-59)."""
    return (int(minuto) // int(segmento_min)) % slots_por_periodo(segmento_min, periodo_min)


def amostra_selecionada(minuto: int, segmento_min: int, periodo_min: int, fase: int) -> bool:
    '''A regra da campanha, em uma linha:
           (m // SEGMENTO_MIN) % (PERIODO // SEGMENTO_MIN) == FASE

    `minuto` é o MINUTO DA HORA do início do segmento (0-59) — ancorar na hora
    (e não em minutos desde a meia-noite) mantém o padrão idêntico em toda hora
    e casa com o resto do pipeline, que já agrupa por hora (top-K, _hora_do_token).
    Com SEGMENTO_MIN=5 / PERIODO=10 / FASE=0 → :00,:10,:20,:30,:40,:50.'''
    return indice_slot(minuto, segmento_min, periodo_min) == int(fase)


def _minuto_do_token(p) -> int:
    """Minuto da hora (0-59) do token seg_AAAAMMDD_HHMMSS; None se não casar."""
    m = _RE_SEG_TS.search(Path(p).name)
    if not m:
        return None
    try:
        return int(m.group(2)[2:4])
    except (ValueError, IndexError):
        return None


def _eh_skip(p) -> bool:
    """Segmento já marcado como fora da amostra."""
    return Path(p).name.endswith(SUFIXO_SKIP)


def _marcar_skip(p) -> bool:
    """Renomeia seg_X.mp4 → seg_X_skip.mp4 (descarte NÃO-destrutivo). Falha é
    não-fatal: o segmento simplesmente segue disponível na próxima passada."""
    p = Path(p)
    if _eh_skip(p):
        return True
    alvo = p.with_name(p.name[: -len(".mp4")] + SUFIXO_SKIP)
    try:
        p.rename(alvo)
        return True
    except Exception as e:
        log.warning("[amostragem] não consegui marcar %s como fora da amostra (%s).", p.name, e)
        return False


def filtrar_amostragem(prontos: dict) -> dict:
    '''Aplica a amostra sistemática ANTES do porteiro/pontuação — não faz sentido
    gastar YOLO num segmento que nunca vai subir. Os que ficam de fora são
    RENOMEADOS (não apagados) e somem depois pela retenção normal.

    AMOSTRAGEM_MODO != "sistematica" → devolve `prontos` intacto.'''
    if AMOSTRAGEM_MODO != "sistematica":
        return prontos
    saida, n_sel, n_skip = {}, 0, 0
    for cam_id, segs in prontos.items():
        mantidos = []
        for p in segs:
            minuto = _minuto_do_token(p)
            if minuto is None:
                mantidos.append(p)       # sem token de relógio → não dá pra amostrar; segue
                continue
            if amostra_selecionada(minuto, SEGMENTO_MIN, AMOSTRAGEM_PERIODO_MIN, AMOSTRAGEM_FASE):
                mantidos.append(p)
                n_sel += 1
            else:
                _marcar_skip(p)
                n_skip += 1
        saida[cam_id] = mantidos
    if n_sel or n_skip:
        log.info("[amostragem] sistemática · período=%dmin · fase=%d · %d na amostra · "
                 "%d fora (renomeado %s, expira pela retenção).",
                 AMOSTRAGEM_PERIODO_MIN, AMOSTRAGEM_FASE, n_sel, n_skip, SUFIXO_SKIP)
    return saida


# ═════════════════════════════════════════════════════════════════════════════
# HEARTBEAT — o pulso do Pi (Fase 52)
#
# ⚠️ Tudo aqui é FIRE-AND-FORGET. Nenhuma função deste bloco pode levantar
# exceção para quem chama, e nenhuma pode demorar. A captura é o produto; o
# heartbeat é telemetria. Se houver conflito, a captura ganha, sempre.
# ═════════════════════════════════════════════════════════════════════════════
# Estado por câmera entre um pulso e o outro: serve para responder "o segmento
# está CRESCENDO?" — que é a única prova real de captura (um Hikvision pode
# responder no socket e entregar imagem preta ou congelada; o RTSP "ok" mente).
_HB_CAM_ANTERIOR: dict = {}
_HB_FALHAS: dict = {}          # cam_id → falhas de conexão desde o último envio
# O pulso periódico roda em thread e o de encerramento roda síncrono: os dois
# podem chamar _saude_camera ao mesmo tempo. Sem trava, um sobrescreveria a
# leitura anterior do outro e o "cresceu?" daria FALSO — o painel mostraria
# "sem sinal" numa câmera perfeitamente saudável.
_HB_LOCK = __import__("threading").Lock()


def _device_id() -> str:
    '''ID estável deste Pi. Preferimos o SERIAL da máquina (sobrevive a
    reinstalação do cartão); se não houver, geramos um UUID e persistimos em
    disco na primeira execução.'''
    try:
        with open("/proc/cpuinfo", "r") as fh:
            for linha in fh:
                if linha.lower().startswith("serial"):
                    serial = linha.split(":", 1)[1].strip()
                    if serial and set(serial) != {"0"}:
                        return f"pi-{serial[-12:]}"
    except Exception:
        pass
    alvo = SAIDA_DIR / ".device_id"
    try:
        if alvo.exists():
            v = alvo.read_text().strip()
            if v:
                return v
        novo = f"edge-{__import__('uuid').uuid4().hex[:12]}"
        alvo.write_text(novo)
        return novo
    except Exception:
        return DEVICE_ID or "edge-desconhecido"


def _cpu_temp_c():
    """Temperatura da CPU em °C, ou None se o SO não expõe."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as fh:
            return round(int(fh.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def _uptime_s():
    try:
        with open("/proc/uptime", "r") as fh:
            return int(float(fh.read().split()[0]))
    except Exception:
        return None


def _disco_metricas() -> tuple:
    """(GB livres, % usado). (None, None) se não der para medir."""
    try:
        u = shutil.disk_usage(str(SAIDA_DIR))
        return round(u.free / 1e9, 2), round((u.used / u.total) * 100.0, 1)
    except Exception:
        return None, None


def _saude_camera(cam) -> dict:
    '''Saúde de UMA câmera. `gravando` NÃO é "o RTSP respondeu" — é "o segmento
    mais novo foi tocado agora há pouco E cresceu (ou surgiu um arquivo novo)".
    É o único sinal que distingue captura real de stream congelado.'''
    info = {"cam_id": cam.id, "nome": getattr(cam, "nome", None) or cam.id,
            "gravando": False, "ultimo_segmento_em": None,
            "ultimo_segmento_bytes": None,
            "falhas": int(_HB_FALHAS.get(cam.id, 0))}
    try:
        segs = list(cam.seg_dir.glob("seg_*.mp4"))
        if not segs:
            return info
        novo = max(segs, key=lambda p: p.stat().st_mtime)
        st = novo.stat()
        info["ultimo_segmento_em"] = datetime.fromtimestamp(
            st.st_mtime).astimezone().isoformat()
        info["ultimo_segmento_bytes"] = st.st_size

        with _HB_LOCK:
            ant = _HB_CAM_ANTERIOR.get(cam.id) or {}
            arquivo_novo = ant.get("path") != str(novo)
            cresceu = (not arquivo_novo) and st.st_size > int(ant.get("bytes") or 0)
            recente = (time.time() - st.st_mtime) <= CAPTURA_STALL_S
            # Sem leitura anterior (1º pulso do run) confiamos só na recência.
            info["gravando"] = bool(recente and (cresceu or arquivo_novo or not ant))
            _HB_CAM_ANTERIOR[cam.id] = {"path": str(novo), "bytes": st.st_size}
    except Exception as e:
        log.debug("[heartbeat] saúde da %s falhou (ignorado): %s", cam.id, e)
    return info


def enviar_heartbeat(cameras, estado: str, turno_janela: str = None,
                     turno_deadline: str = None) -> None:
    '''Envia o pulso — UM por processo_id (câmeras de postos diferentes viram
    heartbeats diferentes). NUNCA levanta, NUNCA demora: timeout curto e
    `except Exception` cobrindo tudo, inclusive o login.'''
    if not HEARTBEAT_ENABLE:
        return
    try:
        por_processo = {}
        for cam in (cameras or []):
            por_processo.setdefault(cam.processo_id, []).append(cam)
        if not por_processo:
            return
        livre_gb, uso_pct = _disco_metricas()
        base = {
            "device_id": _device_id(),
            "runner_versao": RUNNER_VERSAO,
            "estado": estado,
            "disco_livre_gb": livre_gb,
            "disco_uso_pct": uso_pct,
            "cpu_temp_c": _cpu_temp_c(),
            "uptime_s": _uptime_s(),
            "turno_janela": turno_janela,
            "turno_deadline": turno_deadline,
        }
        headers = headers_auth()          # cacheado; pode levantar → coberto
        for processo_id, cams in por_processo.items():
            corpo = dict(base)
            corpo["processo_id"] = processo_id
            corpo["cameras"] = [_saude_camera(c) for c in cams]
            r = requests.post(f"{API_URL}/edge/heartbeat", headers=headers,
                              json=corpo, timeout=HEARTBEAT_TIMEOUT_S)
            if r.status_code in (200, 201):
                for c in cams:            # zera o contador só após envio aceito
                    _HB_FALHAS[c.id] = 0
            else:
                log.info("[heartbeat] backend respondeu HTTP %s (ignorado; a captura "
                         "não é afetada).", r.status_code)
    except Exception as e:
        # Backend fora do ar, rede caída, login expirado: registra e SEGUE.
        log.info("[heartbeat] não enviado (%s) — captura segue normalmente.", e)


def _captura_em_andamento() -> bool:
    '''Existe um --capturar vivo agora? Lê o PID do lockfile e checa se o
    processo existe (sinal 0 não mata ninguém).

    NÃO tentamos adquirir o lock para descobrir isso: um flock, mesmo por um
    instante, faria um --capturar que está subindo desistir — o heartbeat
    derrubaria a captura, exatamente o que ele nunca pode fazer.'''
    try:
        alvo = SAIDA_DIR / ".capturar.lock"
        if not alvo.exists():
            return False
        pid = int((alvo.read_text() or "").strip() or 0)
        if pid <= 0 or pid == os.getpid():
            return False
        os.kill(pid, 0)          # não envia sinal; só testa a existência
        return True
    except (ProcessLookupError, ValueError, PermissionError):
        return False
    except Exception:
        return False


def heartbeat_avulso(cameras) -> int:
    '''--heartbeat: manda UM pulso e sai. É o que mantém o painel vivo 24/7,
    inclusive fora do turno — sem isto, um Pi MORTO e um Pi saudável em
    repouso ficam idênticos na tela até o turno abrir no dia seguinte.

    Pensado para um systemd timer de poucos minutos: processo curto, sem
    estado, sem lock (não concorre com a captura). O estado reportado é o
    HONESTO: se há captura viva, "capturando"; dentro do turno sem captura,
    "ocioso" (o backend trata isso como problema — deveria estar gravando);
    fora do turno, "fora_de_turno".'''
    janela_txt = None
    dentro = False
    try:
        janelas, dias, feriados = carregar_config_turno()
        agora = datetime.now().astimezone()
        dec = resolver_turno(agora, janelas, dias, feriados, TURNO_TOLERANCIA_MIN)
        dentro = dec.acao in ("capturar", "aguardar")
        if dec.janela_idx is not None:
            ini_j, fim_j = janelas[dec.janela_idx]
            janela_txt = f"{ini_j.strftime('%H:%M')}-{fim_j.strftime('%H:%M')}"
    except Exception as e:
        # Turno mal configurado não pode impedir o pulso: sem ele, o painel
        # perde a única prova de que o Pi está vivo.
        log.info("[heartbeat] turno não resolvido (%s) — mando o pulso mesmo assim.", e)

    if _captura_em_andamento():
        estado = "capturando"
    elif dentro:
        estado = "ocioso"          # dentro do turno e nada gravando = suspeito
    else:
        estado = "fora_de_turno"
    log.info("[heartbeat] pulso avulso · estado=%s%s", estado,
             f" · janela {janela_txt}" if janela_txt else "")
    enviar_heartbeat(cameras, estado, janela_txt)
    return 0


def _hb_thread(cameras, estado: str, janela: str = None, deadline: str = None) -> None:
    """Dispara o envio em thread daemon: nem o timeout curto pode segurar o
    supervisor de captura por um instante que seja."""
    if not HEARTBEAT_ENABLE:
        return
    try:
        import threading
        threading.Thread(target=enviar_heartbeat,
                         args=(cameras, estado, janela, deadline),
                         name="heartbeat", daemon=True).start()
    except Exception as e:
        log.debug("[heartbeat] thread não iniciada (ignorado): %s", e)


# ── Fase 37: guarda de segmento aberto + horas completas + disco ─────────────
_captura_ativa = False   # setado pelo --capturar; relaxa o filtro de hora fora dele


def _disco_livre_gb(path=None) -> float:
    try:
        return shutil.disk_usage(str(path or SAIDA_DIR)).free / 1e9
    except Exception:
        return float("inf")


def _segmento_fechado(p) -> bool:
    '''True se o arquivo NÃO está mais sendo escrito (mtime parado há
    SEGMENTO_FECHADO_S). Nunca tocar no segmento em gravação = fim do
    "moov atom not found" em todos os modos.'''
    try:
        return (time.time() - Path(p).stat().st_mtime) >= SEGMENTO_FECHADO_S
    except FileNotFoundError:
        return False


def _hora_do_token(p) -> tuple | None:
    '''(AAAA,MM,DD,HH) do token seg_ do nome, ou None se não casar.'''
    m = _RE_SEG_TS.search(Path(p).name)
    if not m:
        return None
    d, t = m.group(1), m.group(2)
    try:
        return (int(d[0:4]), int(d[4:6]), int(d[6:8]), int(t[0:2]))
    except ValueError:
        return None


def _segmentos_prontos(cameras) -> dict:
    '''Fase 37: por câmera, os segmentos SEGUROS de processar agora.
    Regras: (a) arquivo FECHADO (mtime velho — o ffmpeg não escreve mais);
    (b) durante a captura, só HORAS COMPLETAS: uma hora entra quando o relógio
    já passou dela E nenhum arquivo daquela hora (em NENHUMA câmera) ainda
    está aberto — senão o top-K da hora rodaria com metade dos candidatos e
    o par da outra câmera ficaria para trás. Fora da captura (--processar
    noturno), o filtro de hora é inócuo (todas as horas já passaram).'''
    agora = datetime.now()
    hora_atual = (agora.year, agora.month, agora.day, agora.hour)
    abertos_por_hora: set = set()
    arquivos: dict = {}
    for cam in cameras:
        # `.sel.mp4` = já selecionado (vai pelo caminho de retry) e `_skip.mp4` =
        # descartado pela amostra sistemática (Fase 51) — nenhum dos dois volta
        # a competir aqui.
        segs = sorted(p for p in cam.seg_dir.glob("seg_*.mp4")
                      if not p.name.endswith(".sel.mp4") and not _eh_skip(p))
        arquivos[cam.id] = segs
        for p in segs:
            if not _segmento_fechado(p):
                h = _hora_do_token(p)
                if h is not None:
                    abertos_por_hora.add(h)
    prontos: dict = {}
    for cam in cameras:
        lst = []
        for p in arquivos[cam.id]:
            if not _segmento_fechado(p):
                continue
            h = _hora_do_token(p)
            if h is not None:
                if h in abertos_por_hora:
                    continue                      # irmão da hora ainda gravando
                if _captura_ativa and h >= hora_atual:
                    continue                      # hora ainda em andamento
            lst.append(p)
        prontos[cam.id] = lst
    return prontos


def _parse_gravado_em(clip) -> str | None:
    '''Extrai o instante REAL de início do segmento a partir do nome do arquivo
    (`seg_AAAAMMDD_HHMMSS[_roi].mp4`). Devolve ISO 8601 com timezone local do Pi
    (`America/Sao_Paulo` por padrão). Se o nome não casar (ex.: clip do
    --once/loop), devolve None — backend grava NULL.

    Fase 1 multi-câmera: backend usará isso pra cruzar segmentos de cam1+cam2
    do MESMO instante (próximas fases).'''
    try:
        m = _RE_SEG_TS.search(Path(clip).name)
        if not m:
            return None
        from datetime import datetime
        d, t = m.group(1), m.group(2)
        local = datetime.now().astimezone().tzinfo   # TZ do Pi (vê o /etc/timezone)
        dt = datetime(
            int(d[0:4]), int(d[4:6]), int(d[6:8]),
            int(t[0:2]), int(t[2:4]), int(t[4:6]),
            tzinfo=local,
        )
        return dt.isoformat()
    except Exception as e:
        log.warning("[%s] _parse_gravado_em falhou: %s", Path(clip).name, e)
        return None


def _post_video(clip, processo_id, headers, cam_id=None, gravado_em=None,
                score=None, selecao=None, presenca=None):
    '''POST multipart do clipe (campo 'file'). Reabre o arquivo a cada chamada
    (necessário para a retentativa em 401). Fase 1 multi-câmera: manda também
    `cam_id` e `gravado_em` como form-data (só se não-nulos). Fase 22: `score`
    (0-100 da pontuação de atividade) e `selecao` ('topk'|'calibracao'|'retry')
    p/ auditoria da seleção no backend — opcionais, backend antigo ignora.'''
    with open(clip, "rb") as fh:
        files = {"file": (clip.name, fh, "video/mp4")}   # campo 'file' = videos.upload
        data = {}
        if cam_id:
            data["cam_id"] = cam_id
        if gravado_em:
            data["gravado_em"] = gravado_em
        if score is not None:
            data["score"] = str(score)
        if selecao:
            data["selecao"] = selecao
        # Fase 108: o perfil de presença viaja como JSON num campo de formulário.
        # Backend que não conhece o campo IGNORA (FastAPI descarta form-data que
        # não está declarada) — por isso dá para mandar hoje e ler depois.
        if presenca:
            data["presenca"] = json.dumps(presenca, ensure_ascii=False)
        return requests.post(
            f"{API_URL}/processos/{processo_id}/videos",
            headers=headers,
            files=files,
            data=(data or None),
            timeout=300,
        )


def _subir_direto(clip, processo_id, headers, cam_id, gravado_em, score, selecao,
                  presenca=None) -> bool:
    '''Fase 48 — upload DIRETO ao Supabase Storage, sem passar o vídeo pelo
    backend do Render (corta o egress do Render). 3 passos: (1) pede a URL
    assinada ao backend [JSON], (2) faz PUT do arquivo DIRETO no Storage via
    supabase-py (edge → Supabase), (3) registra na inbox de lote [JSON]. True =
    subiu; False = falhou (segmento MANTIDO p/ retry, sem perda).'''
    from supabase import create_client
    nome = clip.name
    payload = {"cam_id": cam_id or "cam", "nome": nome}

    def _post(rota, corpo, hdrs):
        return requests.post(f"{API_URL}/processos/{processo_id}/{rota}",
                             headers=hdrs, json=corpo, timeout=120)

    # 1) URL assinada (renova sessão 1x em 401)
    r1 = _post("segmentos/upload-url", payload, headers)
    if r1.status_code == 401:
        headers = headers_auth(forcar=True)
        r1 = _post("segmentos/upload-url", payload, headers)
    if r1.status_code not in (200, 201):
        log.error("[upload-direto] upload-url falhou (HTTP %s): %s", r1.status_code, r1.text[:200])
        return False
    b1 = r1.json() or {}
    if b1.get("status") == "duplicado":
        log.info("[upload-direto] %s já existe no Storage — nada a subir.", nome)
        return True
    bucket, storage_path, token = b1.get("bucket"), b1.get("storage_path"), b1.get("token")
    if not (bucket and storage_path and token):
        log.error("[upload-direto] resposta sem bucket/storage_path/token: %s", b1)
        return False

    # 2) PUT DIRETO no Storage (edge → Supabase; NÃO passa pelo Render)
    with open(clip, "rb") as fh:
        dados = fh.read()
    sb_up = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    alvo = sb_up.storage.from_(bucket)
    try:
        alvo.upload_to_signed_url(
            storage_path, token, dados, file_options={"content-type": "video/mp4"},
        )
    except TypeError:
        # storage3 antigo: assinatura sem file_options
        alvo.upload_to_signed_url(storage_path, token, dados)

    # 3) registrar na inbox de lote (JSON pequeno)
    reg = {"storage_path": storage_path, "cam_id": cam_id or "cam", "nome": nome}
    if score is not None:
        reg["score"] = float(score)
    if selecao:
        reg["selecao"] = selecao
    # Fase 108: perfil de presença. O modelo Pydantic da inbox IGNORA chave
    # desconhecida (extra='ignore' é o padrão do Pydantic v2), então mandar
    # hoje não quebra o backend de hoje.
    if presenca:
        reg["presenca"] = presenca
    r3 = _post("segmentos/registrar", reg, headers)
    if r3.status_code == 401:
        headers = headers_auth(forcar=True)
        r3 = _post("segmentos/registrar", reg, headers)
    if r3.status_code in (200, 201) and (r3.json() or {}).get("ok"):
        return True
    log.error("[upload-direto] registrar falhou (HTTP %s): %s", r3.status_code, r3.text[:200])
    return False


def enviar_clipe(clip, processo_id, headers, aguardar_job: bool = True,
                 cam_id=None, gravado_em=None, score=None, selecao=None,
                 presenca=None) -> bool:
    '''Sobe o clipe ao `processo_id` usando `headers` já autenticados (ver
    headers_auth) — NÃO faz login aqui. Com aguardar_job=False (lote): confirma só
    o recebimento (200/201 + job_id) e retorna True sem poll. Em 401, renova a
    sessão (headers_auth(forcar=True)) e tenta UMA vez mais.'''
    # Fase 48: no caminho de LOTE (cam_id presente), sobe DIRETO ao Storage —
    # o vídeo não passa pelo Render (economia de bandwidth). Falha → MANTÉM p/
    # retry; p/ voltar ao modo antigo, EDGE_UPLOAD_DIRETO=off.
    if EDGE_UPLOAD_DIRETO and cam_id:
        try:
            log.info("Enviando %s (%.1f MB) DIRETO ao Storage → processo %s ...",
                     clip.name, clip.stat().st_size / 1e6, processo_id)
            ok = _subir_direto(clip, processo_id, headers, cam_id, gravado_em, score,
                               selecao, presenca=presenca)
            if ok:
                log.info("Upload direto aceito (inbox de lote) · sem tráfego pelo Render.")
            else:
                log.warning("[%s] %s: upload direto FALHOU → MANTIDO p/ retry.", cam_id, clip.name)
            return ok
        except Exception as e:
            log.error("Upload direto falhou (%s) — MANTIDO p/ retry. Para voltar ao modo "
                      "antigo (pelo backend), defina EDGE_UPLOAD_DIRETO=off.", e)
            return False
    try:
        log.info("Enviando %s (%.1f MB) → processo %s ...",
                 clip.name, clip.stat().st_size / 1e6, processo_id)
        r = _post_video(clip, processo_id, headers, cam_id=cam_id, gravado_em=gravado_em,
                        score=score, selecao=selecao, presenca=presenca)
        if r.status_code == 401:
            log.warning("HTTP 401 — sessão expirada; renovando e tentando de novo...")
            headers = headers_auth(forcar=True)
            r = _post_video(clip, processo_id, headers, cam_id=cam_id, gravado_em=gravado_em,
                            score=score, selecao=selecao, presenca=presenca)

        if r.status_code in (200, 201):
            body = r.json() or {}
            job_id = body.get("job_id")
            # Fase 6+: o upload do edge (com cam_id) cai na INBOX de segmentos e
            # responde {"ok": true, "modo": "lote", "status": "pendente"|"duplicado"}
            # — SEM job_id e SEM job para acompanhar (o processamento é disparado
            # depois, em lote, pelo /lote/concluido). Trate isso como SUCESSO,
            # senão o segmento fica em retry infinito e nunca é processado.
            if body.get("modo") == "lote" or (not job_id and body.get("ok")):
                log.info("Upload aceito (inbox de lote) · status=%s", body.get("status"))
                return True
            log.info("Upload aceito · job_id=%s", job_id)
            if not job_id:
                log.warning("Backend não devolveu job_id. Resposta: %s", r.text[:300])
                return False
            if not aguardar_job:
                return True   # lote: recebimento confirmado, segue p/ o próximo segmento
            log.info("Acompanhando o job (poll a cada 1.5s, teto %ds)...", POLL_TIMEOUT_S)
            t_poll = time.monotonic()
            while not _parar:
                if time.monotonic() - t_poll > POLL_TIMEOUT_S:
                    log.warning("Poll excedeu %ds — seguindo sem aguardar o fim do job.", POLL_TIMEOUT_S)
                    return False
                jr = requests.get(f"{API_URL}/jobs/{job_id}", headers=headers, timeout=30)
                if jr.status_code == 404:
                    log.error("Job %s não encontrado (404) — pode ter expirado/sumido.", job_id)
                    return False
                if jr.status_code != 200:
                    log.error("GET /jobs falhou (HTTP %d): %s", jr.status_code, jr.text[:200])
                    return False
                j = jr.json() or {}
                status = str(j.get("status") or j.get("estado") or "?")
                etapa  = j.get("etapa_atual", "?")
                pct    = j.get("progresso_pct", "?")
                log.info("  job %s · %s · etapa=%s · %s%%", job_id, status, etapa, pct)
                if status in ("concluido", "concluído", "erro", "error", "failed", "done"):
                    terminou_ok = ("conclu" in status) or (status == "done")
                    (log.info if terminou_ok else log.error)("Job finalizou com status: %s", status)
                    return terminou_ok
                time.sleep(1.5)
            return False   # interrompido por sinal
        elif r.status_code in (401, 403):
            log.error("HTTP %d — auth/empresa: confira EMAIL/SENHA e se o usuário pertence à "
                      "empresa dona do processo %s. Resp: %s", r.status_code, processo_id, r.text[:200])
        elif r.status_code == 404:
            log.error("HTTP 404 — processo inexistente (%s) ou rota /processos/.../videos "
                      "ausente. Resp: %s", processo_id, r.text[:200])
        else:
            log.error("Upload falhou (HTTP %d): %s", r.status_code, r.text[:300])
        return False
    except Exception as e:
        log.exception("Falha no upload de %s: %s", clip.name, e)
        return False


def _cmd_recorte(clip, saida, vf, crf, fps):
    '''Monta o comando ffmpeg do recorte. `-r fps` só entra quando fps>0.
    Fase 43: com RECORTE_NICE, prefixa `nice`/`ionice` e limita `-threads 2`
    p/ não starvar as capturas na CPU do Pi durante o processar-durante.'''
    cmd = ["ffmpeg", "-y", "-i", str(clip), "-vf", vf, "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", str(int(crf))]
    if RECORTE_NICE:
        cmd += ["-threads", "2"]
    if fps and fps > 0:
        cmd += ["-r", str(fps)]
    cmd.append(str(saida))
    if RECORTE_NICE:
        prefixo = []
        if shutil.which("ionice"):
            prefixo += ["ionice", "-c", "3"]      # best-effort idle no I/O
        if shutil.which("nice"):
            prefixo += ["nice", "-n", "15"]       # baixa prioridade de CPU
        cmd = prefixo + cmd
    return cmd


def _precisa_reencode(size_bytes) -> bool:
    '''True se o arquivo passou do teto de upload (RECORTE_MAX_MB).'''
    return size_bytes > RECORTE_MAX_MB * 1e6


def recortar_roi(clip, cam):
    '''Recorta o vídeo para a ROI da câmera — só essa área sobe; o resto é descartado.
    Re-encode com libx264 (crop exige reprocessar pixels) comprimindo p/ caber
    no teto de 50MB do Supabase free (Fase 38: RECORTE_CRF/FPS). Sem ROI →
    devolve o clip original. Falha no recorte → None (NÃO envia o quadro inteiro).'''
    if cam.roi is None:
        return clip
    if shutil.which("ffmpeg") is None:
        log.error("[%s] ffmpeg ausente — não dá p/ recortar a ROI; segmento NÃO enviado.", cam.id)
        return None
    # Fase 43: input ausente/minúsculo = origem sumiu ou segmento truncado por
    # um stall de câmera. Pular ANTES do ffmpeg (senão gasta minutos e devolve
    # saída vazia, virando um "rc=0 falhou" confuso ou um "rc=254 No such file").
    try:
        tam_in = clip.stat().st_size
    except FileNotFoundError:
        log.warning("[%s] %s: origem ausente — pulando recorte (nada a enviar).",
                    cam.id, clip.name)
        return None
    if tam_in < RECORTE_MIN_KB * 1024:
        log.warning("[%s] %s: origem só %.0f KB (< %.0f KB) — provável segmento "
                    "truncado (stall). Pulando recorte.", cam.id, clip.name,
                    tam_in / 1024, RECORTE_MIN_KB)
        return None
    x, y, w, h = cam.roi
    saida = clip.with_name(clip.stem + "_roi.mp4")
    # crop por expressão iw/ih → independe da resolução; trunc p/ dimensões pares (yuv420p)
    vf = f"crop=trunc(iw*{w}/2)*2:trunc(ih*{h}/2)*2:trunc(iw*{x}):trunc(ih*{y})"

    def _run(crf, fps):
        cmd = _cmd_recorte(clip, saida, vf, crf, fps)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            log.error("[%s] Recorte ROI estourou o timeout.", cam.id)
            saida.unlink(missing_ok=True)
            return False
        saida_ok = saida.exists() and saida.stat().st_size > 0
        if r.returncode != 0:
            log.error("[%s] Recorte ROI: ffmpeg falhou (rc=%s). Últimas linhas:\n%s",
                      cam.id, r.returncode, "\n".join((r.stderr or "").strip().splitlines()[-6:]))
            saida.unlink(missing_ok=True)
            return False
        if not saida_ok:
            # ffmpeg saiu 0 mas não gerou saída válida → input provavelmente
            # corrompido/truncado (stall de câmera). Mensagem separada p/ não
            # confundir com falha real do ffmpeg.
            log.warning("[%s] Recorte ROI: ffmpeg terminou OK mas a saída ficou "
                        "vazia/ausente — input provavelmente corrompido (stall). "
                        "Segmento pulado.", cam.id)
            saida.unlink(missing_ok=True)
            return False
        return True

    log.info("[%s] Recortando ROI (%s) crf=%d fps=%s → %s", cam.id,
             ",".join(f"{v:.3f}" for v in cam.roi), RECORTE_CRF,
             (f"{RECORTE_FPS:g}" if RECORTE_FPS > 0 else "orig"), saida.name)
    if not _run(RECORTE_CRF, RECORTE_FPS):
        return None

    tam_mb = saida.stat().st_size / 1e6
    if _precisa_reencode(saida.stat().st_size):
        crf2 = min(RECORTE_CRF + 6, 34)
        log.warning("[%s] Recorte %.1fMB > teto %.0fMB — re-encode de resgate "
                    "(crf=%d, 10fps)...", cam.id, tam_mb, RECORTE_MAX_MB, crf2)
        if not _run(crf2, 10):
            return None
        tam_mb = saida.stat().st_size / 1e6
        if _precisa_reencode(saida.stat().st_size):
            log.error("[%s] Recorte %.1fMB AINDA acima do teto de 50MB do Supabase "
                      "free mesmo comprimido — REDUZA o segmento (SEGMENTO_MIN=10 → "
                      "~20MB) ou a ROI. Segmento MANTIDO p/ retry.", cam.id, tam_mb)
            saida.unlink(missing_ok=True)
            return None
    return saida


def ciclo_modo_A(cam) -> None:
    '''Um ciclo do caminho ativo p/ uma câmera: grava_clipe(cam) → recortar_roi →
    enviar_clipe() (headers via headers_auth — um login serve p/ todos).'''
    clip = gravar_clipe(cam)
    if clip is None:
        log.warning("[%s] Ciclo MODO A sem clipe — nada a enviar.", cam.id)
        return
    alvo = recortar_roi(clip, cam)   # só a ROI sobe (ou o clipe inteiro se sem ROI)
    if alvo is None:
        log.warning("[%s] Recorte ROI falhou — nada enviado neste ciclo.", cam.id)
        return
    # Fase 1 multi-câmera: passa origem (cam_id) e instante de início (gravado_em).
    # `edge_clip_*` não casa o regex de `seg_AAAAMMDD_HHMMSS`; nesse caminho
    # (--once/loop), aproximamos por DURACAO_CLIPE_S atrás do agora (a gravação
    # acabou de terminar). Para --capturar/--processar, o regex já bate.
    from datetime import datetime, timedelta
    g = _parse_gravado_em(clip)
    if not g:
        g = (datetime.now().astimezone() - timedelta(seconds=DURACAO_CLIPE_S)).isoformat()
    ok = enviar_clipe(
        alvo, cam.processo_id, headers_auth(),
        cam_id=cam.id, gravado_em=g,
    )
    if alvo != clip:
        alvo.unlink(missing_ok=True)   # remove o recorte temporário
    log.info("[%s] Ciclo MODO A %s.", cam.id,
             "concluído" if ok else "encerrado com pendência (ver logs)")


# ─────────────────────────────────────────────────────────────────────────────
# MODO B — preservado, INATIVO (Blocos 2, 3, 6). Reutiliza tudo; só monta+loga.
# ─────────────────────────────────────────────────────────────────────────────
captura_meta = {}   # metadados da última captura (p/ payload do MODO B)


def capturar_amostras() -> list:
    '''Bloco 2: lê o stream por DURACAO_CAPTURA_S e amostra a cada
    INTERVALO_AMOSTRAGEM_S (relógio de parede), com reconexão. Atualiza
    captura_meta. Retorna list[Amostra] (pessoas=[]).'''
    global captura_meta
    amostras = []
    _url = escolher_url()
    cap = abrir_rtsp(_url)
    estado = {"falhas": 0, "backoff": 1.0}

    fps_origem = fps_seguro(cap)
    t0 = time.monotonic()
    prox_amostra = 0.0
    frame_idx = 0
    W = H = None

    log.info("Capturando ~%ds, amostrando a cada %.1fs ...", DURACAO_CAPTURA_S, INTERVALO_AMOSTRAGEM_S)
    while time.monotonic() - t0 < DURACAO_CAPTURA_S and not _parar:
        ok, frame, cap = ler_frame_resiliente(cap, _url, estado)
        if not ok:
            continue
        frame_idx += 1
        if W is None:
            H, W = frame.shape[:2]
        decorrido = time.monotonic() - t0
        if decorrido >= prox_amostra:
            amostras.append(Amostra(frame_idx=frame_idx, tempo_s=round(decorrido, 2),
                                    frame_bgr=frame.copy(), pessoas=[]))
            prox_amostra += INTERVALO_AMOSTRAGEM_S
            log.info("  amostra #%d em t=%.1fs", len(amostras), decorrido)

    cap.release()
    captura_meta = {
        "iniciada_em": datetime.now(timezone.utc).isoformat(),
        "fps_origem": round(fps_origem, 2),
        "intervalo_amostragem_s": INTERVALO_AMOSTRAGEM_S,
        "largura": int(W or 0), "altura": int(H or 0),
    }
    log.info("Captura concluída · %d amostras · %dx%d · ~%.1f FPS",
             len(amostras), W or 0, H or 0, fps_origem)
    return amostras


def rodar_yolo(amostras) -> None:
    '''Bloco 3: roda yolo.track por amostra, filtra por AREA_MIN_RATIO e preenche
    `pessoas`. Import de ultralytics sob demanda. Só roda se RODAR_YOLO=True.'''
    if not RODAR_YOLO:
        log.info("RODAR_YOLO=False → pulando YOLO. (Ligue p/ preencher 'pessoas' no MODO B.)")
        return
    if not amostras:
        log.warning("Sem amostras — nada para inferir.")
        return

    from ultralytics import YOLO   # import sob demanda (lib pesada no Pi)
    log.info("Carregando YOLO %s (CPU)...", YOLO_MODEL)
    yolo = YOLO(YOLO_MODEL)

    n_pessoas_total = 0
    for a in amostras:
        res = yolo.track(a.frame_bgr, persist=True, classes=[0],
                         conf=YOLO_CONF_MIN, tracker=TRACKER_CONFIG, verbose=False)
        pessoas = []
        b = res[0].boxes
        if b is not None and b.id is not None:
            boxes = b.xyxy.cpu().numpy()
            ids   = b.id.cpu().numpy().astype(int)
            hh, ww = a.frame_bgr.shape[:2]
            area_min_px = AREA_MIN_RATIO * (ww * hh)
            areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            mask = areas >= area_min_px
            for i, (box, tid) in enumerate(zip(boxes[mask], ids[mask])):
                x1, y1, x2, y2 = box.astype(int)
                cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
                pessoas.append({
                    "track_id": int(tid),
                    "bbox": (int(x1), int(y1), int(x2), int(y2)),
                    "centro": (cx, cy),
                    "zona": None,                  # sem ROI na borda
                    "rotulo": f"P{i + 1}",
                })
        a.pessoas = pessoas
        n_pessoas_total += len(pessoas)

    log.info("YOLO concluído · %d detecções somadas em %d amostras", n_pessoas_total, len(amostras))


def montar_payload_edge(amostras, meta) -> dict:
    '''Bloco 6: monta o payload do contrato POST /edge/ingest (frames ANOTADOS
    em base64 + metadados). Mesmos parâmetros da nuvem (1024/q85).'''
    return {
        "processo_id": PROCESSO_ID,
        "device_id": DEVICE_ID,
        "captura": meta or {
            "iniciada_em": datetime.now(timezone.utc).isoformat(),
            "fps_origem": 0.0, "intervalo_amostragem_s": INTERVALO_AMOSTRAGEM_S,
            "largura": 0, "altura": 0,
        },
        "amostras": [
            {
                "tempo_s": a.tempo_s,
                "frame_jpeg_b64": frame_para_base64(
                    anotar_frame_com_ids(a.frame_bgr, a.pessoas) if a.pessoas else a.frame_bgr,
                    max_lado=1024, qualidade=85),
                "pessoas": [
                    {"track_id": p["track_id"], "bbox": list(p["bbox"]), "rotulo": p["rotulo"]}
                    for p in a.pessoas
                ],
            }
            for a in amostras
        ],
    }


def ciclo_modo_B() -> None:
    '''Um ciclo do MODO B (INATIVO): captura → YOLO → monta payload → salva JSON e
    AVISA que POST /edge/ingest ainda não existe. Nunca envia (stub).'''
    amostras = capturar_amostras()
    rodar_yolo(amostras)
    payload = montar_payload_edge(amostras, captura_meta)
    destino = SAIDA_DIR / "edge_ingest_payload.json"
    destino.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Payload /edge/ingest montado · %d amostras · %.1f MB · salvo em %s",
             len(payload["amostras"]), destino.stat().st_size / 1e6, destino.resolve())
    log.warning("MODO B é STUB: o endpoint POST %s ainda NÃO existe — payload apenas "
                "montado e salvo (não enviado). Para ativar no futuro, implemente o "
                "endpoint e troque este aviso pelo POST.", EDGE_INGEST_URL)


# ─────────────────────────────────────────────────────────────────────────────
# CAPTURA EM LOTE (--capturar) + PROCESSAMENTO NOTURNO (--processar)
# Operação diária: grava o turno em segmentos; de madrugada filtra PRESENÇA na
# borda (porteiro YOLO), sobe só os segmentos com gente e limpa o cartão. A
# classificação de atividade continua 100% no backend (VLM + Lean).
# ─────────────────────────────────────────────────────────────────────────────
_yolo_modelo = None   # cache do modelo (carregado 1x no --processar)


def _carregar_yolo():
    '''Carrega o YOLO uma vez (import de ultralytics SOB DEMANDA) e cacheia p/ reuso
    em todos os segmentos. Só é chamado no --processar — captura/check não dependem.'''
    global _yolo_modelo
    if _yolo_modelo is None:
        from ultralytics import YOLO   # import sob demanda (lib pesada no Pi)
        log.info("Carregando YOLO %s (CPU) — porteiro de presença...", YOLO_MODEL)
        _yolo_modelo = YOLO(YOLO_MODEL)
    return _yolo_modelo


def _cmd_captura(cam, dur_s: int) -> list:
    '''Monta o comando ffmpeg de captura em segmentos de UMA câmera.

    Pontos-chave para SINCRONIA entre câmeras:
      • -segment_atclocktime 1: corta no RELÓGIO DE PAREDE (múltiplos de
        segment_time desde a meia-noite local), não relativo ao início do
        stream. As duas câmeras passam a cortar nos MESMOS instantes.
      • -strftime 1 + nome com %H%M%S: o nome carimba o horário REAL do corte
        → os arquivos das câmeras batem no mesmo segundo.
      • -reset_timestamps 1: cada segmento começa do zero (cada .mp4 é
        independente).
    Modos:
      • copy (default): rápido/leve, mas o corte cai no próximo keyframe — exige
        GOP pequeno e IGUAL nas duas câmeras p/ alinhar de verdade.
      • reencode (CAPTURA_REENCODE=true): força keyframe exato no corte
        (-force_key_frames) → alinhamento perfeito sem depender da câmera, mas
        custa CPU do Pi.
    '''
    seg_s = int(SEGMENTO_MIN * 60)
    padrao = str(cam.seg_dir / "seg_%Y%m%d_%H%M%S.mp4")
    base = [
        "ffmpeg", "-y",
        "-rtsp_transport", "tcp",
        "-i", cam.url,
        "-t", str(dur_s), "-an",
    ]
    if CAPTURA_REENCODE:
        # Reencoda forçando keyframe exatamente nos múltiplos de seg_s →
        # corte perfeito, alinhado entre câmeras, independente do GOP da câmera.
        enc = [
            "-c:v", "libx264", "-preset", CAPTURA_REENCODE_PRESET,
            "-crf", str(CAPTURA_REENCODE_CRF), "-pix_fmt", "yuv420p",
            "-force_key_frames", f"expr:gte(t,n_forced*{seg_s})",
        ]
    else:
        enc = ["-c:v", "copy"]
    seg = ["-f", "segment", "-segment_time", str(seg_s)]
    if CAPTURA_SEGMENT_ATCLOCK:
        seg += ["-segment_atclocktime", "1"]
    seg += ["-reset_timestamps", "1", "-strftime", "1", padrao]
    return base + enc + seg


# ── Trava de concorrência do --capturar (Fase 51) ───────────────────────────
# Cenário REAL da campanha: o timer da janela dispara às 06:00 e, no mesmo boot,
# o timer de "recuperação pós-queda" dispara junto. Dois --capturar em paralelo
# = dois ffmpeg gravando na MESMA pasta com o mesmo padrão de nome: segmentos
# corrompidos e o supervisor de um matando o ffmpeg do outro.
#
# flock() é a trava certa aqui: o kernel a libera sozinho quando o processo
# morre, então lock ÓRFÃO (PID morto, queda de energia) é recuperado
# automaticamente — sem precisar de heurística de "PID vivo?" nem de limpeza
# manual. O PID vai gravado no arquivo só para o humano saber quem segura.
_LOCK_FH = None


def adquirir_lock_captura(caminho=None) -> tuple:
    '''Tenta travar --capturar. Retorna (ok, mensagem).
    O file handle fica em global de propósito: fechá-lo liberaria a trava, então
    ele precisa viver enquanto o processo viver.'''
    global _LOCK_FH
    alvo = Path(caminho) if caminho else (SAIDA_DIR / ".capturar.lock")
    if fcntl is None:
        return True, f"sem fcntl nesta plataforma — seguindo SEM trava ({alvo.name})"
    try:
        alvo.parent.mkdir(parents=True, exist_ok=True)
        fh = open(alvo, "a+")
    except Exception as e:
        return True, f"não consegui abrir o lockfile ({e}) — seguindo SEM trava"
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        try:
            fh.seek(0)
            dono = (fh.read() or "").strip() or "?"
        except Exception:
            dono = "?"
        fh.close()
        return False, f"outro --capturar já está rodando (PID {dono}) — nada a fazer"
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except Exception:
        pass          # o PID é conveniência; a trava real é o flock
    _LOCK_FH = fh
    return True, f"lock adquirido (PID {os.getpid()}) em {alvo}"


def capturar_segmentos(cameras, duracao_s: float = None,
                       turno_janela: str = None, turno_deadline: str = None) -> int:
    '''--capturar: grava JANELA_CAPTURA_H horas em segmentos de SEGMENTO_MIN
    minutos, UM ffmpeg POR CÂMERA em PARALELO. Os segmentos são cortados no
    RELÓGIO DE PAREDE (ver _cmd_captura), então as câmeras geram segmentos da
    MESMA janela de tempo, com nomes que batem no mesmo segundo — base para o
    cruzamento das imagens depois.

    Supervisor: roda até o fim da janela (deadline único, compartilhado por
    todas as câmeras). Se o ffmpeg de uma câmera MORRER antes do deadline
    (stream caiu, câmera reiniciou), ele é RELANÇADO com o tempo restante — uma
    câmera instável não para mais silenciosa com 1 só segmento. Todas encerram
    juntas no deadline.
    Retorna o total de segmentos novos somando as câmeras.'''
    if shutil.which("ffmpeg") is None:
        log.error("ffmpeg não encontrado no PATH. Instale: sudo apt-get install -y ffmpeg")
        return 0

    if SEGMENTO_MIN not in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60):
        log.warning("SEGMENTO_MIN=%d não divide 60 — o corte por relógio fica "
                    "menos previsível. Prefira 1,2,5,10,15,20,30.", SEGMENTO_MIN)

    # Fase 51: com turno configurado, `duracao_s` vem do DEADLINE DE RELÓGIO
    # (fim da janela menos agora) resolvido em main(); sem turno, cai no
    # comportamento de sempre (duração fixa JANELA_CAPTURA_H).
    janela_s = int(JANELA_CAPTURA_H * 3600) if duracao_s is None else int(max(1, duracao_s))
    horas_log = JANELA_CAPTURA_H if duracao_s is None else janela_s / 3600.0
    deadline = time.monotonic() + janela_s
    modo = "REENCODE (keyframe exato)" if CAPTURA_REENCODE else "copy (keyframe da câmera)"
    log.info("Captura: %.1fh · segmentos de %dmin · corte no relógio=%s · modo=%s · %d câmera(s)",
             horas_log, SEGMENTO_MIN, CAPTURA_SEGMENT_ATCLOCK, modo, len(cameras))

    # Estado por câmera: proc vivo + contagem inicial de segmentos.
    estado = {}
    for cam in cameras:
        cam.seg_dir.mkdir(parents=True, exist_ok=True)
        estado[cam.id] = {
            "cam": cam,
            "proc": None,
            "n_antes": len(list(cam.seg_dir.glob("seg_*.mp4"))),
            "lancamentos": 0,
            "launch_ts": 0.0,
        }

    def _lancar(cam, restante_s):
        cmd = _cmd_captura(cam, int(restante_s))
        log.info("[%s] ffmpeg captura (restante %.0fs) → %s", cam.id, restante_s, cam.seg_dir.resolve())
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        _procs_ffmpeg.append(proc)
        return proc

    def _ultima_atividade_s(st) -> float:
        '''Segundos desde a última escrita de segmento da câmera. Usa o mtime do
        arquivo de segmento mais recente; se nenhum ainda, usa o lançamento.'''
        segs = list(st["cam"].seg_dir.glob("seg_*.mp4"))
        if not segs:
            return time.time() - st["launch_ts"] if st["launch_ts"] else 0.0
        ultimo = max(s.stat().st_mtime for s in segs)
        return max(0.0, time.time() - ultimo)

    def _matar(proc):
        try:
            proc.kill()
            proc.communicate(timeout=5)
        except Exception:
            pass
        try:
            _procs_ffmpeg.remove(proc)
        except ValueError:
            pass

    # Lança TODAS as câmeras juntas (paralelas), o mais próximo possível no tempo.
    for st in estado.values():
        restante = max(1, deadline - time.monotonic())
        st["proc"] = _lancar(st["cam"], restante)
        st["launch_ts"] = time.time()
        st["lancamentos"] += 1

    # ── Fase 37: PROCESSAR DURANTE — a cada PROCESSAR_INTERVALO_MIN, um
    # ciclo de seleção roda EM THREAD (o supervisor não pode parar de vigiar
    # os ffmpeg) sobre as horas já fechadas: pontua, sobe o top-K e apaga a
    # hora. Disco em regime de ~1h de vídeo o dia inteiro. ──
    global _captura_ativa
    _captura_ativa = True
    import threading
    _proc_lock = threading.Lock()

    def _ciclo_durante():
        if not _proc_lock.acquire(blocking=False):
            log.info("[durante] ciclo anterior ainda rodando — pulando este.")
            return
        try:
            log.info("[durante] processando horas fechadas · disco livre %.1fGB",
                     _disco_livre_gb())
            processar_segmentos(cameras)
        except Exception as e:
            log.error("[durante] ciclo falhou (não-fatal, tento no próximo): %s", e)
        finally:
            _proc_lock.release()

    prox_ciclo = (time.monotonic() + PROCESSAR_INTERVALO_MIN * 60
                  if PROCESSAR_DURANTE else None)
    prox_alerta_disco = 0.0
    # Fase 52: pulso inicial + cadência. Em THREAD, para que nem o timeout de
    # 5s do POST possa segurar o supervisor que vigia os ffmpeg.
    _hb_thread(cameras, "capturando", turno_janela, turno_deadline)
    prox_hb = time.monotonic() + HEARTBEAT_INTERVALO_MIN * 60

    # Supervisor: vigia até o deadline; relança quem MORRER ou TRAVAR.
    while not _parar and time.monotonic() < deadline:
        time.sleep(2.0)
        # Fase 37: dispara o ciclo de processamento em background.
        if prox_ciclo is not None and time.monotonic() >= prox_ciclo:
            prox_ciclo = time.monotonic() + PROCESSAR_INTERVALO_MIN * 60
            threading.Thread(target=_ciclo_durante, name="proc_durante",
                             daemon=True).start()
        # Fase 52: pulso periódico durante a captura.
        if time.monotonic() >= prox_hb:
            prox_hb = time.monotonic() + HEARTBEAT_INTERVALO_MIN * 60
            _hb_thread(cameras, "capturando", turno_janela, turno_deadline)
        # Fase 37: alerta claro de disco baixo (1× a cada 10 min).
        if time.monotonic() >= prox_alerta_disco:
            prox_alerta_disco = time.monotonic() + 600
            livre = _disco_livre_gb()
            if livre < DISCO_MIN_LIVRE_GB:
                log.error(
                    "DISCO BAIXO: %.1fGB livres (mínimo %.1fGB). A retenção de "
                    "uploads falhos pode estar acumulando — verifique a rede/"
                    "plataforma%s.", livre, DISCO_MIN_LIVRE_GB,
                    "" if PROCESSAR_DURANTE else
                    " ou ative PROCESSAR_DURANTE=true p/ liberar o disco por hora",
                )
        for st in estado.values():
            proc = st["proc"]
            if proc is None:
                continue
            morreu = proc.poll() is not None
            travou = False
            if not morreu:
                # Vivo, mas parou de escrever? (RTSP conectado sem dados → cam2)
                if _ultima_atividade_s(st) > CAPTURA_STALL_S:
                    travou = True
                    log.warning("[%s] sem novos frames há >%.0fs — tratando como TRAVADO.",
                                st["cam"].id, CAPTURA_STALL_S)
            if not morreu and not travou:
                continue  # saudável

            if morreu:
                try:
                    _, err = proc.communicate(timeout=5)
                except Exception:
                    err = ""
                try:
                    _procs_ffmpeg.remove(proc)
                except ValueError:
                    pass
                if proc.returncode not in (0, None):
                    log.error("[%s] ffmpeg caiu (rc=%s). Últimas linhas:\n%s",
                              st["cam"].id, proc.returncode,
                              "\n".join((err or "").strip().splitlines()[-6:]))
            else:  # travou
                _matar(proc)

            st["proc"] = None
            # Fase 52: morreu OU travou = uma falha de conexão desta câmera. O
            # contador vai no próximo pulso e zera quando o envio é aceito.
            _HB_FALHAS[st["cam"].id] = _HB_FALHAS.get(st["cam"].id, 0) + 1
            restante = deadline - time.monotonic()
            if CAPTURA_RELANCAR and restante > CAPTURA_RELANCO_MIN_S and not _parar:
                log.warning("[%s] relançando captura (faltam %.0fs da janela)...",
                            st["cam"].id, restante)
                time.sleep(min(CAPTURA_RELANCO_MIN_S, max(0, restante - 1)))
                if time.monotonic() < deadline and not _parar:
                    st["proc"] = _lancar(st["cam"], max(1, deadline - time.monotonic()))
                    st["launch_ts"] = time.time()
                    st["lancamentos"] += 1

    # Deadline (ou parada): encerra todos os ffmpeg vivos de forma limpa.
    for st in estado.values():
        proc = st["proc"]
        if proc is None:
            continue
        try:
            proc.terminate()
            try:
                proc.communicate(timeout=60)   # deixa fechar o segmento atual
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
        except Exception:
            pass
        try:
            _procs_ffmpeg.remove(proc)
        except ValueError:
            pass

    n_total = 0
    for st in estado.values():
        cam = st["cam"]
        n_novos = max(0, len(list(cam.seg_dir.glob("seg_*.mp4"))) - st["n_antes"])
        log.info("[%s] Captura encerrada · %d segmento(s) novo(s) · %d lançamento(s).",
                 cam.id, n_novos, st["lancamentos"])
        n_total += n_novos
    log.info("Captura em lote concluída · %d segmento(s) novo(s) no total.", n_total)
    # Fase 52: pulso de encerramento da janela — o painel para de esperar
    # captura desta janela sem precisar aguardar o timeout de "sem sinal".
    enviar_heartbeat(cameras, "ocioso", turno_janela, turno_deadline)

    # ── Fase 37: PASSE FINAL — a captura acabou, então o filtro de "hora
    # completa" relaxa e o resto do dia (inclusive a última hora parcial) é
    # processado agora. Espera o ciclo em andamento e o fechamento do último
    # segmento antes. ──
    _captura_ativa = False
    if PROCESSAR_DURANTE and not _parar:
        _proc_lock.acquire()          # espera um ciclo em voo terminar
        _proc_lock.release()
        log.info("[durante] passe FINAL: aguardando %.0fs o último segmento fechar...",
                 SEGMENTO_FECHADO_S)
        time.sleep(SEGMENTO_FECHADO_S)
        try:
            processar_segmentos(cameras)
        except Exception as e:
            log.error("[durante] passe final falhou (%s) — rode --processar manualmente.", e)
    return n_total


def segmento_tem_pessoa(path, roi=None, zonas=None) -> bool:
    '''Porteiro de PRESENÇA: abre o arquivo, amostra a cada INTERVALO_AMOSTRAGEM_S
    (pelo FPS do arquivo) e roda YOLO.predict no frame.

    Estratégia (corrige falso negativo em chão de fábrica):
      • Se `roi` for dada e PORTEIRO_RECORTAR_ROI=true (default), recorta o
        FRAME pra área da ROI ANTES do YOLO. Assim a pessoa aparece em escala
        alta no detector — em vez de ~60×120 px num quadro inteiro de 640×360,
        ela ocupa quase a área toda do recorte. A confiança naturalmente sobe
        e os thresholds deixam de cortar gente real.
      • Thresholds próprios (PORTEIRO_CONF_MIN, PORTEIRO_AREA_MIN_RATIO): MAIS
        PERMISSIVOS que os do pipeline da nuvem. Aqui a pergunta é "tem
        alguém?", não "vou rastrear esta pessoa".
      • Early-exit no 1º frame com pessoa válida (economia).
      • PORTEIRO_LOG_DETALHES=true → loga contagem de amostras/detecções/
        aprovadas quando descartar um segmento, p/ ajudar a calibrar.
      • Fase 28: com `zonas` (polígonos do posto, espaço do vídeo enviado),
        a detecção só vale se a ÂNCORA topo-do-tronco cair dentro de algum
        polígono — transeuntes fora do posto não contam presença.'''
    yolo = _carregar_yolo()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        log.warning("Não abriu o segmento %s — tratando como vazio.", Path(path).name)
        return False
    try:
        fps = fps_seguro(cap)
        passo = max(1, int(round(INTERVALO_AMOSTRAGEM_S * fps)))
        idx = 0
        n_amostras = 0
        n_deteccoes_brutas = 0
        n_aprovadas = 0
        usa_crop = roi is not None and PORTEIRO_RECORTAR_ROI
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if idx % passo == 0:
                n_amostras += 1
                hh, ww = frame.shape[:2]
                # Define o ALVO do YOLO: o recorte da ROI (preferido) ou o quadro inteiro.
                if usa_crop:
                    rx, ry, rw, rh = roi
                    x1 = max(0, int(rx * ww))
                    y1 = max(0, int(ry * hh))
                    x2 = min(ww, int((rx + rw) * ww))
                    y2 = min(hh, int((ry + rh) * hh))
                    alvo = frame[y1:y2, x1:x2]
                    if alvo.size == 0:
                        idx += 1
                        continue
                else:
                    alvo = frame

                res = yolo.predict(alvo, classes=[0], conf=PORTEIRO_CONF_MIN, verbose=False)
                b = res[0].boxes
                if b is not None and len(b) > 0:
                    boxes = b.xyxy.cpu().numpy()
                    n_deteccoes_brutas += len(boxes)
                    ah, aw = alvo.shape[:2]
                    area_min_px = PORTEIRO_AREA_MIN_RATIO * (aw * ah)
                    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
                    valido = areas >= area_min_px
                    # Quando NÃO recortamos, ainda aplicamos o filtro de centro-no-ROI
                    # como no comportamento anterior (compatibilidade do modo legado).
                    if (not usa_crop) and roi is not None:
                        rx, ry, rw, rh = roi
                        cx = (boxes[:, 0] + boxes[:, 2]) / 2
                        cy = (boxes[:, 1] + boxes[:, 3]) / 2
                        dentro = ((cx >= rx * ww) & (cx <= (rx + rw) * ww)
                                  & (cy >= ry * hh) & (cy <= (ry + rh) * hh))
                        valido = valido & dentro
                    # Fase 28/31: só conta quem TOCA as zonas do posto (qualquer
                    # parte do corpo — kpts + âncora).
                    if zonas:
                        polys = _zonas_polys_px(zonas, roi, usa_crop, alvo.shape, frame.shape)
                        valido = valido & _toca_poligono(boxes, _kpts_do_predict(res), polys, alvo.shape)
                    n_aprovadas += int(valido.sum())
                    if bool(valido.any()):
                        return True
            idx += 1
        # Diagnóstico opcional ao descartar — útil pra calibrar os thresholds.
        if PORTEIRO_LOG_DETALHES:
            log.info("[porteiro] %s · VAZIO · amostras=%d · detecções_brutas=%d · "
                     "aprovadas=%d · conf>=%.2f · área>=%.3f%% do %s",
                     Path(path).name, n_amostras, n_deteccoes_brutas, n_aprovadas,
                     PORTEIRO_CONF_MIN, PORTEIRO_AREA_MIN_RATIO * 100,
                     "ROI recortado" if usa_crop else "quadro")
        return False
    finally:
        cap.release()


# ─────────────────────────────────────────────────────────────────────────────
# SELEÇÃO TOP-K POR HORA (Fase 22) — o porteiro binário vira porteiro com NOTA:
# pontua cada segmento (presença + movimento), agrupa por PAR cam1+cam2 (slot
# de relógio) e sobe só os K mais ativos de cada hora + 1 calibração mediana.
# ─────────────────────────────────────────────────────────────────────────────
def _perfil_do_segmento(seg, cam, zonas_map) -> dict | None:
    '''O perfil de presença de UM segmento, ou None quando o perfil está
    desligado / o arquivo não abriu.

    Grava o sidecar `.presenca.json` ao lado do segmento: o backend ainda não
    lê o sinal, e sem o arquivo a medição de hoje se perderia junto com o vídeo
    quando ele subir e for apagado.'''
    if not PRESENCA_PERFIL:
        return None
    try:
        p = pontuar_segmento(seg, cam.roi, zonas=_zonas_da_cam(zonas_map, cam))
    except Exception as e:
        # Não-fatal, e por um motivo: o perfil é CONTEXTO. Perder o contexto
        # não pode custar o segmento — sem ele o backend segue funcionando
        # exatamente como funcionava antes.
        log.warning("[%s] perfil de presença falhou em %s (não-fatal): %s",
                    cam.id, Path(seg).name, e)
        return None
    perfil = (p or {}).get("presenca")
    if not perfil:
        return None
    if PRESENCA_SIDECAR:
        _gravar_sidecar(seg, perfil)
    if perfil.get("fora_da_zona"):
        # O log grita este caso porque ele é o erro que motivou tudo isto: o
        # backend vai chamar de "posto vazio" um trecho em que havia gente.
        log.warning("[%s] %s: HÁ PESSOA NO QUADRO (%.0f%% do tempo) e NENHUMA "
                    "na zona do posto — provável zona mal desenhada, não posto "
                    "vazio.", cam.id, Path(seg).name, 100 * perfil["frac_quadro"])
    elif perfil.get("na_borda"):
        log.info("[%s] %s: pessoa na BORDA da zona (encosta em %.0f%% das "
                 "amostras, tronco dentro em %.0f%%).", cam.id, Path(seg).name,
                 100 * perfil["frac_zona_larga"], 100 * perfil["frac_zona"])
    return perfil


def _nome_sidecar(seg) -> "Path":
    '''`seg_20260818_132000_cam1.mp4` → `seg_20260818_132000_cam1.presenca.json`.

    O nome sai do TOKEN, com os marcadores de fluxo (`.sel`, `_skip`) removidos:
    o segmento é renomeado várias vezes ao longo do processamento, e um sidecar
    preso ao nome do momento em que foi escrito viraria órfão na primeira
    renomeação.'''
    p = Path(seg)
    base = p.name
    for sufixo in (".mp4",):
        if base.endswith(sufixo):
            base = base[: -len(sufixo)]
    for marcador in (".sel", "_skip"):
        if base.endswith(marcador):
            base = base[: -len(marcador)]
    return p.with_name(base + ".presenca.json")


def _gravar_sidecar(seg, perfil: dict) -> None:
    '''Escreve o perfil ao lado do segmento. Nunca fatal: um disco cheio não
    pode impedir o upload do vídeo, que é o que realmente importa.'''
    try:
        _nome_sidecar(seg).write_text(json.dumps(perfil, ensure_ascii=False),
                                      encoding="utf-8")
    except Exception as e:
        log.warning("[presenca] não deu para gravar o sidecar de %s: %s",
                    Path(seg).name, e)


def _slot_segmento(path) -> str | None:
    '''Slot de RELÓGIO do segmento: token seg_YYYYMMDD_HHMMSS arredondado ao
    grid de SEGMENTO_MIN. Com `-c copy` o corte cai no keyframe SEGUINTE à
    marca do relógio, então cam1=09:20:01 e cam2=09:20:00 têm tokens
    diferentes — o slot arredondado junta os dois no MESMO par (mesma lógica
    da janela de 6 min do backend). None = nome fora do padrão.'''
    m = _RE_SEG_TS.search(Path(path).name)
    if not m:
        return None
    from datetime import datetime
    d, t = m.group(1), m.group(2)
    try:
        dt = datetime(int(d[0:4]), int(d[4:6]), int(d[6:8]),
                      int(t[0:2]), int(t[2:4]), int(t[4:6]))
    except ValueError:
        return None
    grid = max(60, SEGMENTO_MIN * 60)
    slot_epoch = round(dt.timestamp() / grid) * grid
    return datetime.fromtimestamp(slot_epoch).strftime("%Y%m%d_%H%M%S")


def pontuar_segmento(path, roi, zonas=None) -> dict | None:
    '''Pontuador da SELEÇÃO: decodifica o segmento UMA vez e devolve
    {presenca_frac, movimento, mov_norm, n_amostras, score 0-100}.

      • presença  = fração das amostras com pessoa válida (mesmos thresholds
        do porteiro — PORTEIRO_CONF_MIN/AREA_MIN_RATIO — sem early-exit);
      • movimento = média do absdiff em CINZA entre amostras consecutivas do
        recorte ROI reduzido a 160px — "quanto a cena mexe", sem custo de YOLO;
      • score     = 100·[(1−peso)·presença + peso·min(1, mov/SELECAO_MOV_REF)].

    Custo no Pi: cap.grab() nos frames fora da amostra (pula a conversão BGR
    de ~97% dos frames) e YOLO só a cada SELECAO_AMOSTRA_S (6s ⇒ ~100
    inferências num segmento de 10 min) ⇒ ~1 min/segmento no Pi 5.
    None = arquivo não abriu ou sem frames (tratar como vazio).

    Fase 28: com `zonas` (polígonos do posto), presença = só âncora dentro
    das zonas, e o MOVIMENTO é medido só DENTRO da máscara dos polígonos —
    o transeunte na frente do torno deixa de inflar o score/top-K.'''
    yolo = _carregar_yolo()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        log.warning("Não abriu o segmento %s — tratando como vazio.", Path(path).name)
        return None
    try:
        fps = fps_seguro(cap)
        passo = max(1, int(round(SELECAO_AMOSTRA_S * fps)))
        usa_crop = roi is not None and PORTEIRO_RECORTAR_ROI
        idx = n_amostras = n_com_pessoa = 0
        # Uma marca por amostra, em ordem — é daqui que sai a linha do tempo.
        m_quadro: list = []
        m_larga: list = []
        m_zona: list = []
        soma_mov = 0.0
        n_mov = 0
        cinza_ant = None
        mask_mini = None       # Fase 28: máscara 160px das zonas p/ o movimento
        polys_cache = None     # polígonos em px do alvo (constantes no segmento)
        while True:
            if idx % passo != 0:
                if not cap.grab():          # decodifica só o necessário p/ avançar
                    break
                idx += 1
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            n_amostras += 1
            hh, ww = frame.shape[:2]
            if usa_crop:
                rx, ry, rw, rh = roi
                x1 = max(0, int(rx * ww)); y1 = max(0, int(ry * hh))
                x2 = min(ww, int((rx + rw) * ww)); y2 = min(hh, int((ry + rh) * hh))
                alvo = frame[y1:y2, x1:x2]
                if alvo.size == 0:
                    # A amostra existe e não deu para olhar. Marca como SEM
                    # ninguém, senão a linha do tempo desanda: cada marca tem
                    # de corresponder a um passo, ou os segundos mentem.
                    m_quadro.append(False); m_larga.append(False); m_zona.append(False)
                    idx += 1
                    continue
            else:
                alvo = frame

            # movimento (barato): absdiff entre amostras consecutivas, reduzido
            escala = 160.0 / max(1, alvo.shape[1])
            mini = cv2.resize(alvo, (160, max(1, int(alvo.shape[0] * escala))))
            cinza = cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY)
            # Fase 28: máscara das zonas (1x por segmento) — o movimento só
            # conta DENTRO dos polígonos do posto.
            if zonas and mask_mini is None:
                polys_cache = _zonas_polys_px(zonas, roi, usa_crop, alvo.shape, frame.shape)
                mh, mw = cinza.shape[:2]
                fx, fy = mw / max(1, alvo.shape[1]), mh / max(1, alvo.shape[0])
                mask_mini = np.zeros((mh, mw), dtype=np.uint8)
                for poly in polys_cache:
                    p160 = np.round(poly.astype(np.float64) * [fx, fy]).astype(np.int32)
                    cv2.fillPoly(mask_mini, [p160], 255)
                if int(mask_mini.sum()) == 0:
                    mask_mini = None   # zona degenerada → sem máscara
            if cinza_ant is not None and cinza.shape == cinza_ant.shape:
                diff = cv2.absdiff(cinza, cinza_ant)
                if mask_mini is not None:
                    dentro = mask_mini > 0
                    soma_mov += float(diff[dentro].mean()) if dentro.any() else 0.0
                else:
                    soma_mov += float(diff.mean())
                n_mov += 1
            cinza_ant = cinza

            # presença: mesmo YOLO/filtragem do porteiro (sem early-exit).
            # ⭐ Três níveis, UMA inferência — ver o bloco PERFIL DE PRESENÇA.
            res = yolo.predict(alvo, classes=[0], conf=PORTEIRO_CONF_MIN, verbose=False)
            b = res[0].boxes
            v_quadro = v_larga = v_zona = False
            if b is not None and len(b) > 0:
                boxes = b.xyxy.cpu().numpy()
                ah2, aw2 = alvo.shape[:2]
                area_min_px = PORTEIRO_AREA_MIN_RATIO * (aw2 * ah2)
                areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
                valido = areas >= area_min_px
                if (not usa_crop) and roi is not None:
                    rx, ry, rw, rh = roi
                    cx = (boxes[:, 0] + boxes[:, 2]) / 2
                    cy = (boxes[:, 1] + boxes[:, 3]) / 2
                    dentro = ((cx >= rx * ww) & (cx <= (rx + rw) * ww)
                              & (cy >= ry * hh) & (cy <= (ry + rh) * hh))
                    valido = valido & dentro
                # QUADRO: alguém na área enviada, zona nenhuma envolvida. É o
                # que responde "o posto estava REALMENTE vazio?".
                v_quadro = bool(valido.any())
                # Fase 28/31: presença só de quem TOCA as zonas do posto
                # (qualquer parte do corpo — kpts + âncora).
                if zonas:
                    if polys_cache is None:
                        polys_cache = _zonas_polys_px(zonas, roi, usa_crop, alvo.shape, frame.shape)
                    kpts = _kpts_do_predict(res)
                    larga = valido & _toca_poligono(boxes, kpts, polys_cache, alvo.shape)
                    # ESTRITA = a regra da nuvem. É a única comparável com o
                    # veredito que aparece na tela.
                    estrita = valido & _ancora_na_zona(boxes, kpts, polys_cache, alvo.shape)
                    v_larga, v_zona = bool(larga.any()), bool(estrita.any())
                    valido = larga          # o score histórico não muda
                else:
                    # Sem zona desenhada, os três níveis são a mesma coisa —
                    # e é isso mesmo: não há onde a pessoa "não estar".
                    v_larga = v_zona = v_quadro
                if bool(valido.any()):
                    n_com_pessoa += 1
            m_quadro.append(v_quadro)
            m_larga.append(v_larga)
            m_zona.append(v_zona)
            idx += 1

        if n_amostras == 0:
            return None
        presenca = n_com_pessoa / n_amostras
        movimento = (soma_mov / n_mov) if n_mov else 0.0
        mov_norm = min(1.0, movimento / max(0.001, SELECAO_MOV_REF))
        peso = min(1.0, max(0.0, SELECAO_PESO_MOVIMENTO))
        score = round(100.0 * ((1.0 - peso) * presenca + peso * mov_norm), 1)
        passo_s = SELECAO_AMOSTRA_S
        f_quadro = sum(m_quadro) / n_amostras
        f_larga = sum(m_larga) / n_amostras
        f_zona = sum(m_zona) / n_amostras
        return {
            "presenca_frac": round(presenca, 3), "movimento": round(movimento, 2),
            "mov_norm": round(mov_norm, 3), "n_amostras": n_amostras, "score": score,
            # ⭐ O SINAL NOVO. Medida, nunca decisão — quem classifica é o backend.
            "presenca": {
                "versao": 1,
                "passo_s": round(passo_s, 2),
                "n_amostras": n_amostras,
                "com_zona": bool(zonas),
                "frac_quadro": round(f_quadro, 3),
                "frac_zona_larga": round(f_larga, 3),
                "frac_zona": round(f_zona, 3),
                # Vazio DE VERDADE: ninguém em lugar nenhum da área enviada.
                "vazio": f_quadro == 0.0,
                # Tem gente, e não onde a zona foi desenhada. NÃO é posto vazio.
                "fora_da_zona": bool(f_quadro > 0.0 and f_zona == 0.0),
                # A pessoa trabalha na BORDA: encosta na zona, o tronco fica fora.
                "na_borda": bool(f_larga > 0.0 and f_zona < f_larga),
                "maior_ausencia_s": _maior_ausencia(m_zona, passo_s),
                "maior_ausencia_quadro_s": _maior_ausencia(m_quadro, passo_s),
                "janelas_zona": _janelas_de(m_zona, passo_s, PRESENCA_MAX_JANELAS),
                "janelas_quadro": _janelas_de(m_quadro, passo_s, PRESENCA_MAX_JANELAS),
                "janelas_truncadas": bool(
                    len(_janelas_de(m_zona, passo_s, 10**6)) > PRESENCA_MAX_JANELAS
                    or len(_janelas_de(m_quadro, passo_s, 10**6)) > PRESENCA_MAX_JANELAS),
            },
        }
    finally:
        cap.release()


def selecionar_pares(candidatos) -> tuple:
    '''Decide QUAIS pares sobem. `candidatos` = [{"cam","seg","processo_id",
    "slot","score"}], já SEM os vazios. Agrupa por (processo_id, slot) — o PAR
    cam1+cam2 —, score do par = MAX dos lados com presença; agrupa por hora e
    escolhe K = max(SELECAO_MIN_POR_HORA, ceil(SELECAO_QUOTA·n)) por hora.
    A CALIBRAÇÃO (mediana dos não-top, mantém o desperdício visível) entra
    DENTRO do K quando K≥2 — a quota de 40% é 40% de verdade; com K=1 vira
    +1 extra. Retorna (selecionados, descartados) com "motivo" anotado.'''
    import math
    pares: dict = {}
    for c in candidatos:
        chave = (c["processo_id"], c["slot"])
        p = pares.setdefault(chave, {"membros": [], "score": 0.0})
        p["membros"].append(c)
        p["score"] = max(p["score"], c["score"])

    por_hora: dict = {}
    for (pid, slot), p in pares.items():
        por_hora.setdefault((pid, slot[:11]), []).append((slot, p))

    selecionados, descartados = [], []
    for (pid, hora), itens in sorted(por_hora.items()):
        itens.sort(key=lambda it: (-it[1]["score"], it[0]))
        n = len(itens)
        k = min(n, max(SELECAO_MIN_POR_HORA, math.ceil(SELECAO_QUOTA * n)))
        usa_cal = SELECAO_CALIBRACAO_POR_HORA > 0 and n > k
        if usa_cal and k >= 2:
            top, resto = itens[:k - 1], itens[k - 1:]
        else:
            top, resto = itens[:k], itens[k:]
        calibracao = [resto[len(resto) // 2]] if (usa_cal and resto) else []
        cal_slots = {slot for slot, _ in calibracao}
        for slot, p in top:
            selecionados.extend({**m, "motivo": "topk"} for m in p["membros"])
        for slot, p in calibracao:
            selecionados.extend({**m, "motivo": "calibracao"} for m in p["membros"])
        for slot, p in resto:
            if slot in cal_slots:
                continue
            descartados.extend({**m, "motivo": "descartado"} for m in p["membros"])
        log.info("[selecao] proc=%s hora=%sh · %d par(es) → %d subindo (%d topk + %d calibração) "
                 "· %d descartado(s)", str(pid)[:8], hora[-2:], n,
                 len(top) + len(calibracao), len(top), len(calibracao),
                 len(resto) - len(calibracao))
    return selecionados, descartados


def _marcar_selecionado(seg: Path) -> Path:
    '''Marca o segmento como JÁ SELECIONADO (rename → .sel.mp4) ANTES do 1º
    upload. Se o upload falhar, o próximo --processar reconhece o marcador e
    re-sobe DIRETO (sem re-pontuar nem re-competir) — sem isso, um par já
    escolhido que só falhou de rede seria re-sorteado contra uma hora já
    esvaziada e poderia se perder. O nome preserva o token seg_..., então
    pareamento, dedup do backend e retenção continuam funcionando.'''
    if seg.name.endswith(".sel.mp4"):
        return seg
    novo = seg.with_name(seg.name[:-4] + ".sel.mp4")
    try:
        seg.rename(novo)
        return novo
    except Exception as e:
        log.warning("Falha ao marcar %s como selecionado (%s) — seguindo sem marcador.",
                    seg.name, e)
        return seg


def _descartar_nao_selecionado(seg: Path, cam) -> None:
    '''Destino dos NÃO-selecionados (com gente, mas fora do top-K):
    apagar (default) ou mover p/ seg_dir/nao_selecionados/ — subpasta que o
    --processar não re-vê (glob não-recursivo) e a retenção limpa (rglob).'''
    if SELECAO_DESCARTE == "manter":
        destino = cam.seg_dir / "nao_selecionados"
        try:
            destino.mkdir(parents=True, exist_ok=True)
            seg.rename(destino / seg.name)
            return
        except Exception as e:
            log.warning("Falha ao mover %s p/ nao_selecionados (%s) — apagando.", seg.name, e)
    seg.unlink(missing_ok=True)


def _subir_segmento(cam, seg, score=None, selecao=None, presenca=None) -> bool:
    '''Miolo de upload de UM segmento (mesma semântica do caminho clássico):
    recorta a ROI → enviar_clipe → apaga o local se ok / MANTÉM se falhou
    (retry no próximo --processar). True = subiu.'''
    alvo = recortar_roi(seg, cam)
    if alvo is None:
        log.warning("[%s] %s: recorte ROI falhou → MANTIDO p/ retry.", cam.id, seg.name)
        return False
    tam_mb = alvo.stat().st_size / 1e6
    ok = enviar_clipe(
        alvo, cam.processo_id, headers_auth(), aguardar_job=False,
        cam_id=cam.id, gravado_em=_parse_gravado_em(seg),
        score=score, selecao=selecao, presenca=presenca,
    )
    if alvo != seg:
        alvo.unlink(missing_ok=True)           # remove o recorte temporário
    if ok:
        log.info("[%s] %s: enviado (ROI, %.1f MB%s%s).", cam.id, seg.name, tam_mb,
                 f", score={score}" if score is not None else "",
                 f", {selecao}" if selecao else "")
        seg.unlink(missing_ok=True)            # upload OK → apaga
    else:
        log.warning("[%s] %s: upload FALHOU → MANTIDO p/ retry no próximo --processar.",
                    cam.id, seg.name)
    return ok


def limpar_antigos() -> None:
    '''Retenção (rede de segurança), varrendo TODAS as subpastas de SEG_DIR (uma por
    câmera): apaga .mp4 com mtime mais antigo que RETENCAO_DIAS; depois, se o espaço
    livre cair abaixo de MIN_GB_LIVRE, apaga os mais antigos (de qualquer câmera) até
    liberar. Loga o que apagou.'''
    agora = time.time()
    limite_s = RETENCAO_DIAS * 86400
    apagados = 0
    mb_liberados = 0.0

    # 1) por idade (mtime) — rglob varre SEG_DIR e todas as subpastas (cam1, cam2, ...)
    #    Os sidecars de presença entram na MESMA varredura: são minúsculos, mas
    #    o vídeo é apagado depois do upload e eles ficariam para sempre.
    for seg in sorted(list(SEG_DIR.rglob("seg_*.mp4"))
                      + list(SEG_DIR.rglob("seg_*.presenca.json"))):
        try:
            if agora - seg.stat().st_mtime > limite_s:
                mb_liberados += seg.stat().st_size / 1e6
                seg.unlink(missing_ok=True)
                apagados += 1
        except Exception:
            pass

    # 2) por espaço livre (mais antigos primeiro, de qualquer câmera, até liberar)
    try:
        livre_gb = shutil.disk_usage(SEG_DIR).free / 1e9
    except Exception:
        livre_gb = None
    if livre_gb is not None and livre_gb < MIN_GB_LIVRE:
        log.warning("Espaço livre %.1f GB < %.1f GB — apagando segmentos mais antigos "
                    "(todas as câmeras)...", livre_gb, MIN_GB_LIVRE)
        for seg in sorted(SEG_DIR.rglob("seg_*.mp4"), key=lambda p: p.stat().st_mtime):
            try:
                if shutil.disk_usage(SEG_DIR).free / 1e9 >= MIN_GB_LIVRE:
                    break
                mb_liberados += seg.stat().st_size / 1e6
                seg.unlink(missing_ok=True)
                apagados += 1
            except Exception:
                pass

    if apagados:
        log.info("Retenção: %d arquivo(s) apagado(s) (%.1f MB liberados).", apagados, mb_liberados)
    else:
        log.info("Retenção: nada a apagar.")


def processar_segmentos(cameras) -> None:
    '''--processar (madrugada). Dois caminhos:
      • SELECAO_QUOTA >= 1.0 (default) → caminho CLÁSSICO (porteiro binário,
        sobe todo segmento com gente) — inalterado.
      • SELECAO_QUOTA < 1.0 + FILTRAR_PRESENCA=true → SELEÇÃO TOP-K POR HORA
        (Fase 22): pontua tudo, escolhe os pares mais ativos de cada hora
        (+1 calibração mediana) e sobe só esses.'''
    if SELECAO_QUOTA < 1.0 and not FILTRAR_PRESENCA:
        log.warning("SELECAO_QUOTA=%.2f exige FILTRAR_PRESENCA=true — seleção IGNORADA "
                    "(subindo tudo, caminho clássico).", SELECAO_QUOTA)
    # Fase 52: pulso no início e no fim do processamento. `finally` garante o
    # pulso final mesmo se o processamento estourar — o painel não fica preso
    # em "processando" para sempre.
    _hb_thread(cameras, "processando")
    try:
        if SELECAO_QUOTA >= 1.0 or not FILTRAR_PRESENCA:
            return _processar_segmentos_todos(cameras)
        return _processar_segmentos_selecao(cameras)
    finally:
        _hb_thread(cameras, "ocioso")


def _processar_segmentos_selecao(cameras) -> None:
    '''Fase 22 — fluxo em fases: (0) re-envia os .sel.mp4 marcados de runs
    anteriores (já selecionados, só falharam de rede); (1) pontua todos os
    segmentos novos (vazio → apaga, igual ao porteiro); (2) seleciona por
    par/hora; (3) sobe os selecionados (marcando .sel antes do 1º upload) e
    então descarta os não-selecionados. Retenção + sinal de lote no fim.'''
    t0 = time.monotonic()
    try:
        headers_auth()
    except Exception as e:
        log.error("Login Supabase falhou (%s) — não vou subir agora; mantenho os segmentos "
                  "e rodo só a retenção.", e)
        limpar_antigos()
        return

    log.info("SELEÇÃO TOP-K ATIVA · quota=%.0f%% · min/h=%d · calibração/h=%d · "
             "amostra=%.0fs · descarte=%s", SELECAO_QUOTA * 100, SELECAO_MIN_POR_HORA,
             SELECAO_CALIBRACAO_POR_HORA, SELECAO_AMOSTRA_S, SELECAO_DESCARTE)

    # Fase 28: zonas do posto (plataforma) — score conta só o operador.
    zonas_map = baixar_zonas(cameras) if ZONAS_REMOTAS else {}

    # Fase 37: só segmentos SEGUROS — fechados (ffmpeg não escreve mais) e,
    # durante a captura, apenas de horas completas nas DUAS câmeras.
    # Fase 51: e, se a amostra sistemática estiver ligada, só os slots da amostra
    # — ANTES de pontuar (não gastar YOLO em quem nunca vai subir).
    prontos = filtrar_amostragem(_segmentos_prontos(cameras))

    tot_env = tot_falhou = tot_vazio = tot_desc = 0
    candidatos = []
    for cam in cameras:
        if _parar:
            log.info("Parada solicitada — interrompendo o lote.")
            break
        # Fase 0: marcados .sel de runs anteriores → direto pro upload.
        for seg in sorted(cam.seg_dir.glob("seg_*.sel.mp4")):
            if _parar:
                break
            if not _segmento_fechado(seg):
                continue
            ok = _subir_segmento(cam, seg, selecao="retry")
            tot_env += 1 if ok else 0
            tot_falhou += 0 if ok else 1
        # Fase 1: pontuar os novos (vazio → apaga; sem token → sobe direto).
        segs = prontos.get(cam.id, [])
        # "Adiado" = gravando ou de hora incompleta. Fora da amostra (_skip) NÃO
        # é adiamento — é descarte deliberado, já logado por filtrar_amostragem.
        n_adiados = len([p for p in cam.seg_dir.glob("seg_*.mp4")
                         if not p.name.endswith(".sel.mp4") and not _eh_skip(p)]) - len(segs)
        if n_adiados > 0:
            log.info("[%s] %d segmento(s) adiado(s) (gravando ou hora incompleta).",
                     cam.id, n_adiados)
        if not segs:
            log.info("[%s] Sem segmentos novos em %s.", cam.id, cam.seg_dir.resolve())
            continue
        zonas_cam = _zonas_da_cam(zonas_map, cam)
        if zonas_cam and ZONAS_LOG:
            log.info("[%s] Zonas do posto ATIVAS no score (%d polígono(s)).",
                     cam.id, len(zonas_cam))
        log.info("[%s] Pontuando %d segmento(s)%s...", cam.id, len(segs),
                 " · zonas do posto" if zonas_cam else "")
        for seg in segs:
            if _parar:
                break
            try:
                p = pontuar_segmento(seg, cam.roi, zonas=zonas_cam)
            except Exception as e:
                log.warning("[%s] Falha ao pontuar %s (não-fatal, MANTIDO): %s",
                            cam.id, seg.name, e)
                continue
            perfil = (p or {}).get("presenca")
            if perfil and PRESENCA_SIDECAR:
                _gravar_sidecar(seg, perfil)
            # ⚠️ O corte passou a ser "vazio NO QUADRO", não "vazio na zona".
            # Zona mal desenhada apagava o turno inteiro aqui, antes de qualquer
            # olho humano ver o vídeo. Gente fora da zona sobe; quem decide o
            # que ela é, é o backend.
            if p is None or (perfil or {}).get("vazio", p["presenca_frac"] <= 0):
                tot_vazio += 1
                log.info("[%s] %s: vazio → descartado.", cam.id, seg.name)
                seg.unlink(missing_ok=True)
                continue
            slot = _slot_segmento(seg)
            if slot is None:
                # Clipe avulso sem token de relógio: não compete — sobe direto.
                ok = _subir_segmento(cam, seg, score=p["score"], selecao="topk",
                                     presenca=perfil)
                tot_env += 1 if ok else 0
                tot_falhou += 0 if ok else 1
                continue
            candidatos.append({"cam": cam, "seg": seg, "processo_id": cam.processo_id,
                               "slot": slot, "score": p["score"], "presenca": perfil})

    # Fase 2: seleção por par (processo_id + slot) e por hora.
    selecionados, descartados = selecionar_pares(candidatos)

    # Fase 3: sobe os selecionados (marca .sel ANTES do 1º upload); só depois
    # descarta os não-selecionados (se pararmos no meio, nada foi perdido).
    for c in selecionados:
        if _parar:
            log.info("Parada solicitada — interrompendo os uploads.")
            break
        seg = _marcar_selecionado(c["seg"])
        if not seg.exists():
            # Origem sumiu entre a pontuação e o upload (stall/retenção) — nada
            # a enviar; pula sem virar "falha" nem chamar o recorte num arquivo
            # inexistente (Fase 43: evita o rc=254 No such file).
            log.info("[%s] %s: origem sumiu antes do upload — pulando.",
                     c["cam"].id, Path(c["seg"]).name)
            continue
        ok = _subir_segmento(c["cam"], seg, score=c["score"], selecao=c["motivo"],
                             presenca=c.get("presenca"))
        tot_env += 1 if ok else 0
        tot_falhou += 0 if ok else 1
    if not _parar:
        for c in descartados:
            _descartar_nao_selecionado(c["seg"], c["cam"])
            tot_desc += 1

    limpar_antigos()
    dt = time.monotonic() - t0
    log.info("Resumo GERAL (seleção) · %d câmera(s) · enviados=%d · falhos-retidos=%d · "
             "vazios=%d · não-selecionados=%d · %.0fs",
             len(cameras), tot_env, tot_falhou, tot_vazio, tot_desc, dt)

    if tot_env > 0 and not _parar:
        headers = headers_auth()
        for pid in sorted({cam.processo_id for cam in cameras}):
            sinalizar_lote_concluido(pid, headers)


def _processar_segmentos_todos(cameras) -> None:
    '''Caminho CLÁSSICO (pré-Fase 22), SEQUENCIAL por câmera: filtra PRESENÇA, sobe os
    com gente ao cam.processo_id (login ÚNICO via headers_auth, renovado por tempo/401)
    e descarta os vazios. NÃO apaga quando o upload falha (mantém p/ o próximo
    --processar). limpar_antigos() varre todas as subpastas. Resumo por câmera e total.'''
    t0 = time.monotonic()

    # Login ÚNICO (pré-voo). Se falhar, não sobe agora; mantém os segmentos e só limpa.
    try:
        headers_auth()
    except Exception as e:
        log.error("Login Supabase falhou (%s) — não vou subir agora; mantenho os segmentos "
                  "e rodo só a retenção.", e)
        limpar_antigos()
        return

    # Fase 28: zonas do posto também no caminho clássico (porteiro).
    zonas_map = baixar_zonas(cameras) if ZONAS_REMOTAS else {}

    # Fase 37: só segmentos fechados/de horas completas (guarda anti-moov).
    # Fase 51: + amostra sistemática (quando ligada) antes do porteiro.
    prontos = filtrar_amostragem(_segmentos_prontos(cameras))

    tot_env = tot_falhou = tot_vazio = 0
    mb_tot = 0.0
    for cam in cameras:
        if _parar:
            log.info("Parada solicitada — interrompendo o lote.")
            break
        segs = prontos.get(cam.id, [])   # nome = timestamp → ordem cronológica
        if not segs:
            log.info("[%s] Sem segmentos em %s.", cam.id, cam.seg_dir.resolve())
            continue
        log.info("[%s] Processando %d segmento(s) (filtro de presença=%s)...",
                 cam.id, len(segs), FILTRAR_PRESENCA)
        c_env = c_falhou = c_vazio = 0
        c_mb = 0.0
        for seg in segs:
            if _parar:
                log.info("Parada solicitada — interrompendo o lote.")
                break
            try:
                # ⭐ O PERFIL SUBSTITUI O PORTEIRO BINÁRIO. O porteiro respondia
                # sim/não e jogava fora tudo o mais que já tinha visto: onde a
                # pessoa estava e em que segundos. É esse "tudo o mais" que o
                # backend precisa para parar de errar `posto_vazio` — e ele sai
                # da MESMA decodificação, sem chamada nova.
                #
                # ⚠️ O DESCARTE ficou mais conservador de propósito: antes bastava
                # ninguém NA ZONA para o segmento ser apagado, e um polígono mal
                # desenhado apagava o turno inteiro na origem. Agora só se apaga
                # o que está vazio NO QUADRO — ninguém em lugar nenhum. Segmento
                # com gente fora da zona SOBE, e o backend decide.
                perfil = None
                tem = True
                if FILTRAR_PRESENCA:
                    perfil = _perfil_do_segmento(seg, cam, zonas_map)
                    tem = bool(perfil is None or not perfil.get("vazio", False))
                if not tem:
                    c_vazio += 1
                    log.info("[%s] %s: vazio (ninguém no quadro) → descartado.",
                             cam.id, seg.name)
                    seg.unlink(missing_ok=True)            # vazio → apaga
                    continue
                alvo = recortar_roi(seg, cam)              # só a ROI sobe (ou o seg inteiro)
                if alvo is None:
                    c_falhou += 1
                    log.warning("[%s] %s: recorte ROI falhou → MANTIDO p/ retry.", cam.id, seg.name)
                    continue
                tam_mb = alvo.stat().st_size / 1e6
                # Fase 1 multi-câmera: passa cam_id + gravado_em (parseado do
                # nome do SEGMENTO ORIGINAL, não do recorte ROI — o original
                # carrega o timestamp do relógio de parede).
                ok = enviar_clipe(
                    alvo, cam.processo_id, headers_auth(), aguardar_job=False,
                    cam_id=cam.id, gravado_em=_parse_gravado_em(seg),
                    presenca=perfil,
                )
                if alvo != seg:
                    alvo.unlink(missing_ok=True)           # remove o recorte temporário
                if ok:
                    c_env += 1
                    c_mb += tam_mb
                    log.info("[%s] %s: COM gente → enviado (ROI, %.1f MB).", cam.id, seg.name, tam_mb)
                    seg.unlink(missing_ok=True)            # upload OK → apaga
                else:
                    c_falhou += 1
                    log.warning("[%s] %s: upload FALHOU → MANTIDO p/ retry no próximo "
                                "--processar.", cam.id, seg.name)
            except Exception as e:
                log.warning("[%s] Falha ao processar %s (não-fatal): %s", cam.id, seg.name, e)
        log.info("[%s] Resumo · enviados=%d · falhos-retidos=%d · vazios=%d · %.1f MB",
                 cam.id, c_env, c_falhou, c_vazio, c_mb)
        tot_env += c_env
        tot_falhou += c_falhou
        tot_vazio += c_vazio
        mb_tot += c_mb

    limpar_antigos()
    dt = time.monotonic() - t0
    log.info("Resumo GERAL · %d câmera(s) · enviados=%d · falhos-retidos=%d · vazios=%d "
             "· %.1f MB · %.0fs", len(cameras), tot_env, tot_falhou, tot_vazio, mb_tot, dt)

    # Fase 6: lote terminou de subir → dispara o processamento na plataforma.
    # Um sinal por processo DISTINTO entre as câmeras (cada CAMn pode ter o seu
    # CAMn_PROCESSO_ID). Nesse ponto todos os segmentos de todas as câmeras (cam1
    # e cam2) já subiram, então o backend acha os PARES completos no storage e
    # processa 1 por 1, throttle-safe. Só sinaliza se subiu algo neste run;
    # idempotente no backend de qualquer forma (só processa o que está pendente).
    if tot_env > 0 and not _parar:
        headers = headers_auth()
        for pid in sorted({cam.processo_id for cam in cameras}):
            sinalizar_lote_concluido(pid, headers)


def sinalizar_lote_concluido(processo_id: str, headers: dict) -> None:
    '''Avisa a plataforma que o lote deste processo terminou de subir → dispara
    o processamento (o backend pareia cam1+cam2 pelo nome e processa 1 por 1).
    Não-fatal: se falhar, a varredura periódica do backend processa o lote
    sozinha (após ~15min de inatividade) — este sinal só adianta o início.'''
    try:
        r = requests.post(
            f"{API_URL}/processos/{processo_id}/lote/concluido",
            headers=headers, timeout=30,
        )
        r.raise_for_status()
        log.info("Lote concluído sinalizado (processo %s): %s", processo_id, r.json())
    except Exception as e:
        log.warning("Falha ao sinalizar lote do processo %s (a varredura periódica "
                    "cobre): %s", processo_id, e)


# ─────────────────────────────────────────────────────────────────────────────
# Turno + amostragem: leitura da config e inspeção (Fase 51)
# ─────────────────────────────────────────────────────────────────────────────
def carregar_config_turno() -> tuple:
    """(janelas, dias, feriados) do .env. Config inválida sobe
    ConfigTurnoInvalida — quem chama decide entre exit 2 e só reportar."""
    return (parse_janelas(TURNO_JANELAS), parse_dias(TURNO_DIAS), parse_feriados(TURNO_FERIADOS))


def _checar_amostragem_ou_sair() -> None:
    """Valida a amostragem sistemática. Config inválida = exit 2: melhor não
    subir do que rodar 30 dias colhendo uma amostra que não vale nada."""
    if AMOSTRAGEM_MODO not in ("off", "sistematica"):
        log.error("AMOSTRAGEM_MODO=%r inválido — use 'off' ou 'sistematica'.", AMOSTRAGEM_MODO)
        sys.exit(2)
    if AMOSTRAGEM_MODO != "sistematica":
        return
    if not _AM_PER_OK or not _AM_FASE_OK:
        log.error("AMOSTRAGEM_PERIODO_MIN/AMOSTRAGEM_FASE precisam ser inteiros.")
        sys.exit(2)
    erro = validar_amostragem(SEGMENTO_MIN, AMOSTRAGEM_PERIODO_MIN, AMOSTRAGEM_FASE)
    if erro:
        log.error("Amostragem sistemática inválida: %s", erro)
        sys.exit(2)
    if 60 % AMOSTRAGEM_PERIODO_MIN != 0:
        log.warning("AMOSTRAGEM_PERIODO_MIN=%d não divide 60 — o padrão de slots "
                    "REINICIA a cada hora, então a cobertura fica irregular na virada. "
                    "Prefira 5, 10, 12, 15, 20, 30 ou 60.", AMOSTRAGEM_PERIODO_MIN)
    # Empilhar top-K (escolhe por atividade) sobre uma amostra sistemática
    # reintroduz exatamente o viés que a amostra existe para eliminar.
    if SELECAO_QUOTA < 1.0:
        log.warning(
            "⚠️  AMOSTRAGEM_MODO=sistematica JUNTO com SELECAO_QUOTA=%.2f (<1.0): o top-K "
            "escolhe os segmentos MAIS ATIVOS de cada hora, o que REINTRODUZ o viés de "
            "medição que a amostra sistemática existe para eliminar — o %% de tempo "
            "produtivo sairá INFLADO. Para medir de verdade, use SELECAO_QUOTA=1.0.",
            SELECAO_QUOTA)


def imprimir_turno_info(agora: datetime = None) -> int:
    '''--turno-info: mostra como o turno e a amostragem serão interpretados HOJE,
    sem abrir câmera nem gravar. É o que permite validar a config da campanha à
    tarde, sem esperar as 6h da manhã.'''
    agora = agora or datetime.now().astimezone()
    linhas = []
    add = linhas.append

    add("═" * 72)
    add("TURNO · INSPEÇÃO (nenhuma câmera é aberta; nada é gravado)")
    add("═" * 72)
    tzname = agora.tzname() or "?"
    add(f"Agora           : {agora.strftime('%Y-%m-%d %H:%M:%S')}  (fuso {tzname}, "
        f"offset {agora.strftime('%z') or 'n/d'})")
    dow = agora.isoweekday()
    add(f"Dia da semana   : {DOW_PT.get(dow, '?')}({dow})")

    try:
        janelas, dias, feriados = carregar_config_turno()
    except ConfigTurnoInvalida as e:
        add("")
        add(f"❌ CONFIG INVÁLIDA: {e}")
        print("\n".join(linhas))
        return 2

    add(f"TURNO_DIAS      : {TURNO_DIAS or '(vazio = todos)'} → {sorted(dias)}")
    add(f"TURNO_FERIADOS  : {TURNO_FERIADOS or '(nenhum)'}")
    add(f"Tolerância      : {TURNO_TOLERANCIA_MIN:.0f} min antes do início")
    add("")

    if not janelas:
        add("Janelas         : (TURNO_JANELAS vazio) → MODO LEGADO")
        add(f"                  --capturar grava {JANELA_CAPTURA_H:.2f}h a partir da chamada.")
    else:
        add("Janelas de hoje :")
        for i, (ini, fim) in enumerate(janelas):
            ini_dt = datetime.combine(agora.date(), ini, tzinfo=agora.tzinfo)
            fim_dt = datetime.combine(agora.date(), fim, tzinfo=agora.tzinfo)
            dur = (fim_dt - ini_dt).total_seconds()
            marca = "◀ agora" if ini_dt <= agora < fim_dt else ""
            add(f"  [{i}] {ini_dt.strftime('%H:%M:%S')} → {fim_dt.strftime('%H:%M:%S')} "
                f"({_fmt_restante(dur)}) {marca}")

    dec = resolver_turno(agora, janelas, dias, feriados, TURNO_TOLERANCIA_MIN)
    add("")
    add(f"Decisão agora   : {dec.acao.upper()} — {dec.motivo}")
    if dec.acao == "capturar":
        add(f"Deadline        : {dec.fim.strftime('%H:%M:%S')} "
            f"· restam {_fmt_restante(dec.duracao_s)}")
    elif dec.acao == "aguardar":
        add(f"Início          : {dec.inicio.strftime('%H:%M:%S')} "
            f"(dorme {dec.espera_s:.0f}s) · deadline {dec.fim.strftime('%H:%M:%S')} "
            f"· gravaria {_fmt_restante(dec.duracao_s)}")
    elif dec.acao == "legado":
        add(f"Deadline        : agora + {JANELA_CAPTURA_H:.2f}h (duração fixa)")
    else:
        add("Deadline        : — (sai com código 0, sem gravar)")

    # ── Amostragem ──
    add("")
    add("─" * 72)
    add(f"AMOSTRAGEM      : {AMOSTRAGEM_MODO}"
        + (f" · período={AMOSTRAGEM_PERIODO_MIN}min · fase={AMOSTRAGEM_FASE}"
           if AMOSTRAGEM_MODO == "sistematica" else ""))
    add(f"SEGMENTO_MIN    : {SEGMENTO_MIN} min")
    if 60 % SEGMENTO_MIN != 0:
        add(f"  ⚠️  SEGMENTO_MIN={SEGMENTO_MIN} NÃO divide 60 — o corte no relógio de parede "
            f"fica imprevisível.")
        add("      Prefira 1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30 ou 60.")
    if AMOSTRAGEM_MODO == "sistematica":
        erro = validar_amostragem(SEGMENTO_MIN, AMOSTRAGEM_PERIODO_MIN, AMOSTRAGEM_FASE)
        if erro:
            add(f"  ❌ {erro}")
            print("\n".join(linhas))
            return 2
        if 60 % AMOSTRAGEM_PERIODO_MIN != 0:
            add(f"  ⚠️  período {AMOSTRAGEM_PERIODO_MIN}min não divide 60 — o padrão "
                f"reinicia a cada hora.")
        if SELECAO_QUOTA < 1.0:
            add(f"  ⚠️  SELECAO_QUOTA={SELECAO_QUOTA:.2f} empilha top-K sobre a amostra e "
                f"REINTRODUZ viés. Use 1.0 para medir.")
        n = slots_por_periodo(SEGMENTO_MIN, AMOSTRAGEM_PERIODO_MIN)
        cob = (SEGMENTO_MIN / AMOSTRAGEM_PERIODO_MIN) * 60
        add(f"  {n} slot(s) por período · cobertura ≈ {cob:.0f} min por hora")

    # Tabela da PRÓXIMA hora (a hora cheia seguinte ao instante atual).
    prox = (agora.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    add("")
    add(f"Slots da próxima hora ({prox.strftime('%H:00')}–{prox.strftime('%H')}:59):")
    if 60 % SEGMENTO_MIN != 0:
        add("  (SEGMENTO_MIN não divide 60 — tabela aproximada)")
    for minuto in range(0, 60, max(1, SEGMENTO_MIN)):
        rot = f"  {prox.strftime('%H')}:{minuto:02d}"
        if AMOSTRAGEM_MODO != "sistematica":
            add(f"{rot}  SELECIONADO   (amostragem off — todo segmento concorre)")
            continue
        idx = indice_slot(minuto, SEGMENTO_MIN, AMOSTRAGEM_PERIODO_MIN)
        sel = idx == AMOSTRAGEM_FASE
        add(f"{rot}  {'SELECIONADO' if sel else 'pulado     '}   (slot {idx})")
    add("═" * 72)
    print("\n".join(linhas))
    return 0


def _preparar_captura_turno(cameras=None) -> tuple:
    '''Resolve o turno ANTES de abrir qualquer stream e devolve
    (duracao_s, janela_txt, deadline_iso) desta invocação. Pode encerrar o
    processo:
      • exit 0 — dia não útil / feriado / fora de janela (NÃO é falha: o systemd
        não deve marcar o serviço como quebrado);
      • exit 2 — configuração inválida.
    duracao_s = None no modo legado (duração fixa JANELA_CAPTURA_H).

    Fase 52: quando decide SAIR, manda um heartbeat `fora_de_turno` antes —
    é assim que o painel sabe que o Pi está VIVO e ocioso, em vez de morto.'''
    try:
        janelas, dias, feriados = carregar_config_turno()
    except ConfigTurnoInvalida as e:
        log.error("Configuração de turno inválida: %s", e)
        sys.exit(2)

    agora = datetime.now().astimezone()
    dec = resolver_turno(agora, janelas, dias, feriados, TURNO_TOLERANCIA_MIN)

    if dec.acao == "legado":
        return (None, None, None)
    if dec.acao == "sair":
        log.info("Turno · hoje=%s(%d) · agora %s · NÃO capturo: %s",
                 DOW_PT.get(agora.isoweekday(), "?"), agora.isoweekday(),
                 agora.strftime("%H:%M:%S"), dec.motivo)
        enviar_heartbeat(cameras, "fora_de_turno")   # síncrono: vamos sair já
        sys.exit(0)
    if dec.acao == "aguardar":
        log.info("Turno · hoje=%s(%d) · janela %s–%s ainda não abriu · aguardando %.0fs (%s)",
                 DOW_PT.get(agora.isoweekday(), "?"), agora.isoweekday(),
                 dec.inicio.strftime("%H:%M"), dec.fim.strftime("%H:%M"),
                 dec.espera_s, dec.motivo)
        time.sleep(max(0.0, dec.espera_s))
        if _parar:
            log.info("Parada solicitada durante a espera — saindo sem capturar.")
            sys.exit(0)
        agora = datetime.now().astimezone()
        dec = resolver_turno(agora, janelas, dias, feriados, TURNO_TOLERANCIA_MIN)
        if dec.acao != "capturar":
            log.info("Turno · após a espera a janela não está ativa (%s) — saindo.", dec.motivo)
            sys.exit(0)

    log.info("Turno · hoje=%s(%d) · janela %s–%s · agora %s · restam %s · deadline %s",
             DOW_PT.get(agora.isoweekday(), "?"), agora.isoweekday(),
             dec.inicio.strftime("%H:%M") if dec.janela_idx is None
             else datetime.combine(agora.date(), janelas[dec.janela_idx][0]).strftime("%H:%M"),
             dec.fim.strftime("%H:%M"), agora.strftime("%H:%M:%S"),
             _fmt_restante(dec.duracao_s), dec.fim.strftime("%H:%M:%S"))
    janela_txt = None
    if dec.janela_idx is not None:
        ini_j, fim_j = janelas[dec.janela_idx]
        janela_txt = f"{ini_j.strftime('%H:%M')}-{fim_j.strftime('%H:%M')}"
    return (dec.duracao_s, janela_txt, dec.fim.isoformat())


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serviço de borda Kalidash (Pi 5). Grava clipe e sobe pela plataforma (MODO A).")
    parser.add_argument("--check", action="store_true",
                        help="valida câmera(s)/rede (salva 1 frame por câmera) e sai")
    parser.add_argument("--once", action="store_true",
                        help="executa 1 ciclo do MODO atual (todas as câmeras) e sai (debug)")
    parser.add_argument("--capturar", action="store_true",
                        help="grava o turno em segmentos, 1 ffmpeg/câmera em paralelo, e sai")
    parser.add_argument("--processar", action="store_true",
                        help="madrugada: filtra presença, sobe os com gente, limpa e sai")
    parser.add_argument("--heartbeat", action="store_true",
                        help="manda UM pulso de saúde e sai (para um systemd timer "
                             "de poucos minutos; mantém o painel vivo 24/7)")
    parser.add_argument("--turno-info", action="store_true", dest="turno_info",
                        help="mostra turno/janelas/slots resolvidos para hoje e sai "
                             "(não abre câmera, não grava)")
    parser.add_argument("--cam", type=int, default=None, metavar="N",
                        help="mira só a câmera N (ex.: --cam 2); padrão: todas")
    args = parser.parse_args()

    # --turno-info: pura inspeção de configuração. Roda ANTES de carregar câmeras
    # de propósito — deve funcionar num .env sem RTSP válido, que é justamente a
    # situação de quem está montando a campanha.
    if args.turno_info:
        sys.exit(imprimir_turno_info())

    # Config de amostragem é validada ANTES de carregar câmeras: erro de
    # configuração precisa aparecer como erro de configuração, não escondido
    # atrás de um RTSP inválido. Só para quem usa a amostragem — `--check`
    # segue idêntico ao de sempre.
    if args.capturar or args.processar:
        _checar_amostragem_ou_sair()

    # Validação precoce (após argparse p/ --help funcionar sem .env).
    assert MODO in ("A", "B", "C"), f"MODO inválido: {MODO!r} (use 'A', 'B' ou 'C')."
    cameras = carregar_cameras()        # auto-descoberta + fallback; valida e loga cada URL
    if args.cam is not None:
        alvo = f"cam{args.cam}"
        cameras = [c for c in cameras if c.id == alvo]
        if not cameras:
            log.error("Câmera %s não encontrada no .env (defina CAM%d_*).", alvo, args.cam)
            sys.exit(2)
        if args.processar and SELECAO_QUOTA < 1.0:
            log.warning("--cam com SELEÇÃO ativa: a outra câmera fica fora da pontuação — "
                        "os pares desta rodada viram solos (o backend re-pareia se a outra "
                        "câmera subir depois).")
    log.info("CONFIG · MODO=%s · stream=%s · %d câmera(s) · API_URL=%s · saída=%s",
             MODO, "SUB(102)" if USAR_SUBSTREAM else "MAIN(101)",
             len(cameras), API_URL, SAIDA_DIR.resolve())

    # Parada limpa sob systemd.
    signal.signal(signal.SIGINT, _handler_sinal)
    signal.signal(signal.SIGTERM, _handler_sinal)

    # --check: testa TODAS as câmeras (não curto-circuita; salva 1 frame por câmera).
    if args.check:
        ok_todas = True
        for cam in cameras:
            if not checar_conexao(cam):
                ok_todas = False
        sys.exit(0 if ok_todas else 1)

    # --heartbeat: pulso avulso e sai. Não toca em câmera, não pega lock, não
    # concorre com nada — pode rodar a cada poucos minutos o dia inteiro.
    if args.heartbeat:
        sys.exit(heartbeat_avulso(cameras))

    # --capturar: grava todas em paralelo e sai (NÃO depende de ultralytics).
    if args.capturar:
        # Ordem importa: (1) config inválida derruba antes de qualquer efeito
        # colateral; (2) a TRAVA vem antes do turno para que duas invocações
        # simultâneas não durmam as duas na tolerância; (3) só então o turno
        # decide o deadline — tudo isso ANTES de abrir stream.
        ok_lock, msg_lock = adquirir_lock_captura()
        if not ok_lock:
            log.info("Captura NÃO iniciada: %s.", msg_lock)
            sys.exit(0)
        log.info("Captura · %s", msg_lock)
        # None = modo legado (duração fixa). Manda heartbeat `fora_de_turno` e
        # sai com 0 quando não é hora de capturar.
        duracao, janela_txt, deadline_iso = _preparar_captura_turno(cameras)
        capturar_segmentos(cameras, duracao_s=duracao,
                           turno_janela=janela_txt, turno_deadline=deadline_iso)
        return

    # --processar: varre todas em sequência e sai (ultralytics sob demanda).
    if args.processar:
        processar_segmentos(cameras)
        return

    # Despacho por MODO (--once / loop).
    if MODO == "A":
        def ciclo():
            for cam in cameras:
                ciclo_modo_A(cam)
    elif MODO == "B":
        ciclo = ciclo_modo_B            # MODO B inativo (legado, 1 câmera via globais)
    else:  # "C" — não incluído no serviço
        log.error("MODO C (Pi autônomo) não é suportado pelo serviço. Use 'A' (produção) ou 'B' (stub).")
        sys.exit(2)

    # --once: um ciclo (todas as câmeras) e sai.
    if args.once:
        ciclo()
        return

    # Loop de produção contínuo, resiliente (nunca derruba o processo).
    log.info("Loop de produção iniciado (MODO=%s, %d câmera(s)). Ctrl-C / SIGTERM para parar.",
             MODO, len(cameras))
    while not _parar:
        try:
            ciclo()
            # MODO A: a gravação já dura DURACAO_CLIPE_S → segue direto ao próximo
            # clipe, sem buracos longos. Em falha, o except abaixo dá um respiro.
        except Exception as e:
            log.warning("Ciclo falhou (não-fatal): %s", e)
            time.sleep(2.0)
    log.info("Serviço encerrado de forma limpa.")


if __name__ == "__main__":
    main()
