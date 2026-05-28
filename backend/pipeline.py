"""Kalidash Vision · Pipeline Inteligente.

Refatoração do notebook KaliVision_Pipeline_Final.ipynb. A LÓGICA DE IA
(prompts, modelos, regras de aprendizado, auto-validação, isolamento por
contexto) é IDÊNTICA à do notebook. Aqui apenas encapsulamos as células
em funções parametrizáveis chamáveis pelo worker da API.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from groq import Groq
from supabase import Client, create_client
from ultralytics import YOLO

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s · %(levelname)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("kalidash")


# ═════════════════════════════════════════════════════════════════════════
# MODELOS / DEFAULTS — exatamente os mesmos do notebook
# ═════════════════════════════════════════════════════════════════════════
YOLO_MODEL = "yolo11n-pose.pt"
GROQ_MODEL_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_MODEL_ANALISE = "openai/gpt-oss-120b"
GROQ_MODEL_RAPIDO = "llama-3.3-70b-versatile"

YOLO_CONF_MIN = 0.45
AREA_MIN_RATIO = 0.005
TRACKER_CONFIG = "botsort.yaml"

DEFAULT_INTERVALO_AMOSTRAGEM_S = 3.0
DEFAULT_LIMIAR_AUTO_VALIDACAO = 2

DEFAULT_ROIS_CONTEXTO: dict[str, dict[str, Any]] = {
    "PC": {
        "pts_rel": [(0.55, 0.20), (0.75, 0.20), (0.75, 0.78), (0.55, 0.78)],
        "descricao_contexto": "estação com computador",
    },
    "BENCH": {
        "pts_rel": [(0.30, 0.20), (0.51, 0.20), (0.51, 0.78), (0.30, 0.78)],
        "descricao_contexto": "bancada de trabalho manual",
    },
    "BACKGROUND": {
        "pts_rel": [(0.0, 0.0), (1.0, 0.0), (1.0, 0.18), (0.0, 0.18)],
        "descricao_contexto": "área de circulação",
    },
}


# ═════════════════════════════════════════════════════════════════════════
# SCHEMA SQL (igual ao notebook). Use para criar/atualizar o banco.
# ═════════════════════════════════════════════════════════════════════════
SCHEMA_SQL = """
-- Vídeos analisados
create table if not exists videos (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    nome text not null,
    caminho text,
    duracao_s numeric,
    fps numeric,
    largura int,
    altura int,
    total_pessoas int,
    total_eventos int,
    processado_em timestamptz default now()
);

create table if not exists comportamentos (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    label text not null,
    descricao text,
    total_ocorrencias int default 0,
    primeira_observacao timestamptz default now(),
    ultima_observacao timestamptz default now(),
    unique (empresa, processo, label)
);

create table if not exists eventos (
    id uuid primary key default gen_random_uuid(),
    video_id uuid references videos(id) on delete cascade,
    empresa text not null,
    processo text not null,
    pessoa_track_id int not null,
    comportamento_label text not null,
    descricao_bruta text,
    tempo_inicio_s numeric not null,
    tempo_fim_s numeric not null,
    duracao_s numeric generated always as (tempo_fim_s - tempo_inicio_s) stored,
    frame_inicio int,
    frame_fim int,
    bbox_inicio jsonb,
    zona_contexto text,
    n_amostras int default 1,
    confianca numeric default 0.7,
    validado_humano boolean default false,
    validacao_correto boolean,
    label_corrigido text,
    validado_em timestamptz,
    origem_validacao text,
    criado_em timestamptz default now()
);

create table if not exists sugestoes_melhoria (
    id uuid primary key default gen_random_uuid(),
    video_id uuid references videos(id) on delete cascade,
    empresa text not null,
    processo text not null,
    prioridade text,
    area text,
    situacao text,
    causa_provavel text,
    sugestao text,
    impacto_estimado text,
    eventos_relacionados jsonb,
    criado_em timestamptz default now()
);

create table if not exists contexto_processo (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    descricao text,
    atualizado_em timestamptz default now(),
    unique (empresa, processo)
);

alter table sugestoes_melhoria add column if not exists impacto_estimado text;

create index if not exists idx_videos_ctx        on videos(empresa, processo);
create index if not exists idx_comportamentos_ctx on comportamentos(empresa, processo);
create index if not exists idx_eventos_ctx       on eventos(empresa, processo);
create index if not exists idx_eventos_video     on eventos(video_id);
create index if not exists idx_eventos_label     on eventos(comportamento_label);
create index if not exists idx_eventos_pessoa    on eventos(pessoa_track_id);
create index if not exists idx_eventos_origem    on eventos(origem_validacao);
create index if not exists idx_sugestoes_ctx     on sugestoes_melhoria(empresa, processo);
create index if not exists idx_contexto_proc     on contexto_processo(empresa, processo);
"""


# ═════════════════════════════════════════════════════════════════════════
# CLIENTES
# ═════════════════════════════════════════════════════════════════════════
def make_supabase_client(url: str | None = None, key: str | None = None) -> Client:
    url = url or os.environ["SUPABASE_URL"]
    key = key or os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def make_groq_client(api_key: str | None = None) -> Groq:
    api_key = api_key or os.environ["GROQ_API_KEY"]
    return Groq(api_key=api_key)


# ═════════════════════════════════════════════════════════════════════════
# PROGRESSO — callback opcional usado pelo worker
# ═════════════════════════════════════════════════════════════════════════
ProgressCb = Callable[[str, int, str], None]  # (etapa, progresso_pct, mensagem)


def _noop_progress(etapa: str, pct: int, mensagem: str) -> None:
    log.info(f"[{etapa} {pct}%] {mensagem}")


# ═════════════════════════════════════════════════════════════════════════
# MEMÓRIA DO NEGÓCIO (isolada por empresa+processo)
# ═════════════════════════════════════════════════════════════════════════
def carregar_memoria_do_negocio(
    sb: Client,
    empresa: str,
    processo: str,
    limite_eventos: int = 5000,
    top_vocabulario: int = 30,
) -> dict[str, Any]:
    """Lê do Supabase tudo que foi validado por humanos para o par
    (empresa, processo) e monta a memória de aprendizado.
    Isolamento estrito: validações de outros clientes/processos NUNCA
    contaminam esta memória.
    """
    memoria = {
        "vocabulario": [],
        "correcoes_aprendidas": {},
        "descartados": {},
        "total_eventos_validados": 0,
    }

    r = (
        sb.table("eventos")
        .select(
            "comportamento_label, label_corrigido, descricao_bruta, validacao_correto"
        )
        .eq("empresa", empresa)
        .eq("processo", processo)
        .eq("validado_humano", True)
        .limit(limite_eventos)
        .execute()
    )
    eventos = r.data or []
    memoria["total_eventos_validados"] = len(eventos)

    if not eventos:
        log.info(f"Memória vazia para {empresa}/{processo}.")
        return memoria

    confirmados: Counter = Counter()
    descartados: Counter = Counter()
    correcoes_brutas: dict[str, Counter] = {}

    for ev in eventos:
        correto = ev.get("validacao_correto")
        label_orig = ev.get("comportamento_label", "")
        label_corr = ev.get("label_corrigido")
        desc_bruta = (ev.get("descricao_bruta") or "").strip().lower()

        if correto is False:
            descartados[label_orig] += 1
        elif correto is True:
            if label_corr and label_corr != label_orig:
                if desc_bruta:
                    correcoes_brutas.setdefault(desc_bruta, Counter())[label_corr] += 1
            else:
                confirmados[label_orig] += 1

    memoria["correcoes_aprendidas"] = {
        desc: ctr.most_common(1)[0][0] for desc, ctr in correcoes_brutas.items()
    }
    memoria["descartados"] = dict(descartados.most_common(20))

    r2 = (
        sb.table("comportamentos")
        .select("label, descricao")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .execute()
    )
    catalogo_completo = {c["label"]: c.get("descricao", "") for c in (r2.data or [])}

    vocabulario = []
    for label, n in confirmados.most_common(top_vocabulario):
        vocabulario.append(
            {
                "label": label,
                "descricao": catalogo_completo.get(label, label),
                "n_confirmacoes": n,
            }
        )
    memoria["vocabulario"] = vocabulario

    log.info(
        f"Memória · {len(eventos)} eventos validados · "
        f"{len(vocabulario)} labels · "
        f"{len(memoria['correcoes_aprendidas'])} correções · "
        f"{len(memoria['descartados'])} descartes · "
        f"{empresa}/{processo}"
    )
    return memoria


# ═════════════════════════════════════════════════════════════════════════
# DESCRIÇÃO DO PROCESSO (domain priming)
# ═════════════════════════════════════════════════════════════════════════
def resolver_descricao_processo(
    sb: Client, empresa: str, processo: str, descricao: str | None
) -> str:
    """Se descricao for não-vazia, faz upsert no banco e retorna.
    Se for vazia/None, tenta carregar a descrição salva no banco para
    este contexto.
    """
    descricao = (descricao or "").strip()
    if descricao:
        existe = (
            sb.table("contexto_processo")
            .select("id")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .execute()
        )
        if existe.data:
            sb.table("contexto_processo").update(
                {
                    "descricao": descricao,
                    "atualizado_em": datetime.utcnow().isoformat(),
                }
            ).eq("id", existe.data[0]["id"]).execute()
        else:
            sb.table("contexto_processo").insert(
                {"empresa": empresa, "processo": processo, "descricao": descricao}
            ).execute()
        return descricao

    r = (
        sb.table("contexto_processo")
        .select("descricao")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .execute()
    )
    if r.data and (r.data[0].get("descricao") or "").strip():
        return r.data[0]["descricao"].strip()
    return ""


def construir_bloco_processo(descricao: str) -> str:
    if not descricao:
        return ""
    return (
        "DESCRIÇÃO DO PROCESSO (fornecida pelo cliente — use como guia de domínio):\n"
        '"""\n' + descricao + '\n"""\n'
        "Priorize reconhecer os comportamentos mencionados nesta descrição e use "
        "vocabulário alinhado a ela. Ações que claramente NÃO se encaixam nesta "
        "descrição podem ser incomuns/anômalas — descreva-as com atenção.\n\n"
    )


# ═════════════════════════════════════════════════════════════════════════
# FRAME UTILS
# ═════════════════════════════════════════════════════════════════════════
def anotar_frame_com_ids(frame_bgr: np.ndarray, pessoas: list[dict]) -> np.ndarray:
    f = frame_bgr.copy()
    for p in pessoas:
        x1, y1, x2, y2 = p["bbox"]
        cor = (0, 255, 100)
        cv2.rectangle(f, (x1, y1), (x2, y2), cor, 3)
        label = p["rotulo"]
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 1.4, 3)
        cv2.rectangle(f, (x1, y1 - th - 14), (x1 + tw + 14, y1), cor, -1)
        cv2.putText(
            f, label, (x1 + 6, y1 - 6), cv2.FONT_HERSHEY_DUPLEX, 1.4, (0, 0, 0), 3
        )
    return f


def frame_para_base64(frame_bgr: np.ndarray, max_lado: int = 1024, qualidade: int = 85) -> str:
    h, w = frame_bgr.shape[:2]
    if max(h, w) > max_lado:
        escala = max_lado / max(h, w)
        frame_bgr = cv2.resize(frame_bgr, (int(w * escala), int(h * escala)))
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _ponto_em_roi(cx: float, cy: float, polygon: np.ndarray) -> bool:
    return cv2.pointPolygonTest(polygon, (float(cx), float(cy)), False) >= 0


def _build_rois(rois_contexto: dict, w: int, h: int) -> dict:
    rois = {}
    for nome, info in rois_contexto.items():
        polygon = np.array(
            [[int(x * w), int(y * h)] for x, y in info["pts_rel"]], dtype=np.int32
        )
        rois[nome] = {**info, "polygon": polygon}
    return rois


def _zona_contexto(cx: int, cy: int, rois: dict) -> str | None:
    for _, info in rois.items():
        if _ponto_em_roi(cx, cy, info["polygon"]):
            return info["descricao_contexto"]
    return None


# ═════════════════════════════════════════════════════════════════════════
# GROQ CALLS COM RETRY
# ═════════════════════════════════════════════════════════════════════════
def groq_vision_call(
    groq_client: Groq,
    image_b64: str,
    prompt_texto: str,
    json_mode: bool = True,
    max_tokens: int = 1024,
    temperatura: float = 0.2,
    retries: int = 3,
    model: str = GROQ_MODEL_VISION,
) -> str:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_texto},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                },
            ],
        }
    ]
    kwargs: dict[str, Any] = dict(
        model=model,
        messages=messages,
        temperature=temperatura,
        max_completion_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    for tentativa in range(retries):
        try:
            r = groq_client.chat.completions.create(**kwargs)
            return r.choices[0].message.content
        except Exception as e:
            espera = 2**tentativa
            log.warning(f"Groq vision falhou ({e}). Retry em {espera}s...")
            time.sleep(espera)
    raise RuntimeError("Groq vision falhou após retries")


def groq_text_call(
    groq_client: Groq,
    prompt: str,
    model: str | None = None,
    system: str | None = None,
    json_mode: bool = False,
    max_tokens: int = 2048,
    temperatura: float = 0.3,
    retries: int = 3,
) -> str:
    model = model or GROQ_MODEL_ANALISE
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = dict(
        model=model,
        messages=messages,
        temperature=temperatura,
        max_completion_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    for tentativa in range(retries):
        try:
            r = groq_client.chat.completions.create(**kwargs)
            return r.choices[0].message.content
        except Exception as e:
            espera = 2**tentativa
            log.warning(f"Groq text falhou ({e}). Retry em {espera}s...")
            time.sleep(espera)
    raise RuntimeError("Groq text falhou após retries")


# ═════════════════════════════════════════════════════════════════════════
# PROMPTS — VLM (visão) e CLUSTER (agrupar descrições)
# ═════════════════════════════════════════════════════════════════════════
PROMPT_VLM = """Você é um analista de processos industriais observando uma operação.
Na imagem há pessoas marcadas com rótulos P1, P2, ... numa zona vermelha.
Para cada pessoa marcada, descreva em UMA FRASE CURTA (até 10 palavras) o que ela está fazendo.

{bloco_processo}{bloco_vocabulario}REGRAS:
- Foque na AÇÃO (verbo + objeto), não na aparência.
- Use linguagem operacional clara em português ("manipulando peça", "digitando no computador", "andando pelo corredor").
- Se a ação não estiver clara, escreva "ação não identificada".
- NÃO invente ações que não estão visíveis.

CONTEXTO: {contexto_zonas}

Responda APENAS um JSON no formato:
{{"acoes": {{"P1": "...", "P2": "...", ...}}}}"""


PROMPT_CLUSTER = """Você é um analista de processos industriais.
Abaixo está uma lista de descrições de ações observadas num vídeo de operação.
Várias descrições se referem ao MESMO comportamento, mas com palavras diferentes.

{bloco_processo}{bloco_memoria}Sua tarefa: AGRUPAR as descrições em comportamentos únicos e dar para cada um:
- um label canônico em snake_case, curto e operacional (ex: "operar_computador", "manipular_peca")
- uma descrição humana em português

REGRAS:
- Comportamentos como "digitando no PC", "operando o computador", "usando o teclado" devem ter o MESMO label.
- Comportamentos genuinamente diferentes devem ter labels diferentes.
- Use labels descritivos da AÇÃO (verbo+objeto), não da localização.
- Inclua TODAS as descrições da entrada — cada uma cai em algum grupo.
- "ação não identificada" vira o label "acao_indefinida".
- PRIORIDADE MÁXIMA: se um label canônico já validado se aplica, REUSE-O em vez de criar um novo.

Responda APENAS um JSON no formato:
{{
  "comportamentos": [
    {{"label": "operar_computador", "descricao": "Pessoa operando computador / digitando", "descricoes_originais": ["digitando no pc", "operando computador"]}},
    ...
  ]
}}

DESCRIÇÕES OBSERVADAS:
"""


def construir_bloco_vocabulario(memoria: dict, max_itens: int = 20) -> str:
    if not memoria.get("vocabulario"):
        return ""
    linhas = [
        "VOCABULÁRIO OPERACIONAL CONHECIDO deste cliente (use estes termos quando a ação corresponder, mantendo consistência com observações anteriores):"
    ]
    for v in memoria["vocabulario"][:max_itens]:
        linhas.append(f'- {v["descricao"]}')
    linhas.append("")
    linhas.append(
        "Se reconhecer uma das ações conhecidas, descreva usando vocabulário CONSISTENTE com o catálogo acima. Se for ação genuinamente nova, descreva livremente."
    )
    return "\n".join(linhas) + "\n\n"


def construir_bloco_memoria_cluster(
    memoria: dict,
    max_vocab: int = 25,
    max_correcoes: int = 15,
    max_descartes: int = 10,
) -> str:
    blocos: list[str] = []

    if memoria.get("vocabulario"):
        linhas = ["LABELS CANÔNICOS JÁ VALIDADOS por humanos neste cliente:"]
        for v in memoria["vocabulario"][:max_vocab]:
            linhas.append(f'  - {v["label"]}: {v["descricao"]}')
        linhas.append("")
        linhas.append(
            'REGRA DURA: se uma descrição corresponde semanticamente a um destes labels, REUSE o label existente. NÃO crie variantes ("operar_pc" vs "operar_computador" — escolha o validado).'
        )
        blocos.append("\n".join(linhas))

    if memoria.get("correcoes_aprendidas"):
        linhas = [
            "",
            "CORREÇÕES APRENDIDAS de execuções anteriores (modelo havia inferido outro label e o humano corrigiu — use estes labels quando ver descrições parecidas):",
        ]
        for desc, label in list(memoria["correcoes_aprendidas"].items())[:max_correcoes]:
            linhas.append(f'  - descrição "{desc}" → label CORRETO: {label}')
        blocos.append("\n".join(linhas))

    if memoria.get("descartados"):
        linhas = [
            "",
            "LABELS MARCADOS COMO FALSO POSITIVO (humanos descartaram estes labels em execuções anteriores — EVITE criar variantes deles):",
        ]
        for label, n in list(memoria["descartados"].items())[:max_descartes]:
            linhas.append(f"  - {label} (descartado {n}× pelo humano)")
        blocos.append("\n".join(linhas))

    if not blocos:
        return ""
    return "\n".join(blocos) + "\n\n"


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 1 · Detecção + tracking (amostragem temporal)
# ═════════════════════════════════════════════════════════════════════════
@dataclass
class Amostra:
    frame_idx: int
    tempo_s: float
    frame_bgr: np.ndarray
    pessoas: list


def inspecionar_video(video_path: str) -> dict:
    assert Path(video_path).exists(), f"Vídeo não encontrado: {video_path}"
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return {
        "fps": fps,
        "total_frames": total_frames,
        "largura": w,
        "altura": h,
        "duracao_s": total_frames / max(1, fps),
    }


def etapa_detectar_e_amostrar(
    yolo: YOLO,
    video_path: str,
    intervalo_s: float,
    rois_contexto: dict,
    progress_cb: ProgressCb,
) -> tuple[list[Amostra], dict, list[int]]:
    info = inspecionar_video(video_path)
    fps = info["fps"]
    total_frames = info["total_frames"]
    w = info["largura"]
    h = info["altura"]

    rois = _build_rois(rois_contexto, w, h)
    intervalo_frames = max(1, int(intervalo_s * fps))
    area_min_px = AREA_MIN_RATIO * (w * h)

    amostras: list[Amostra] = []
    cap = cv2.VideoCapture(video_path)
    frame_idx = 0
    progress_cb("deteccao", 0, f"Detectando pessoas · {total_frames} frames")

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        results = yolo.track(
            frame,
            persist=True,
            classes=[0],
            conf=YOLO_CONF_MIN,
            tracker=TRACKER_CONFIG,
            verbose=False,
        )
        if (
            frame_idx % intervalo_frames == 0
            and results[0].boxes is not None
            and results[0].boxes.id is not None
        ):
            boxes = results[0].boxes.xyxy.cpu().numpy()
            ids = results[0].boxes.id.cpu().numpy().astype(int)
            areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            mask = areas >= area_min_px
            pessoas = []
            for i, (box, tid) in enumerate(zip(boxes[mask], ids[mask])):
                x1, y1, x2, y2 = box.astype(int)
                cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
                pessoas.append(
                    {
                        "track_id": int(tid),
                        "bbox": (int(x1), int(y1), int(x2), int(y2)),
                        "centro": (cx, cy),
                        "zona": _zona_contexto(cx, cy, rois),
                        "rotulo": f"P{i + 1}",
                    }
                )
            if pessoas:
                amostras.append(
                    Amostra(
                        frame_idx=frame_idx,
                        tempo_s=frame_idx / fps,
                        frame_bgr=frame.copy(),
                        pessoas=pessoas,
                    )
                )
        frame_idx += 1
        if frame_idx % 60 == 0:
            pct = int(frame_idx / max(1, total_frames) * 100)
            progress_cb(
                "deteccao",
                min(pct, 99),
                f"frame {frame_idx}/{total_frames} · {len(amostras)} amostras",
            )

    cap.release()
    ids_unicos = sorted({p["track_id"] for a in amostras for p in a.pessoas})
    progress_cb("deteccao", 100, f"{len(amostras)} amostras · {len(ids_unicos)} pessoas")
    return amostras, info, ids_unicos


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 2 · Análise semântica (VLM)
# ═════════════════════════════════════════════════════════════════════════
def _analisar_amostra_vlm(
    groq_client: Groq,
    amostra: Amostra,
    descricao_processo: str,
    memoria: dict,
) -> dict[int, str]:
    frame_anotado = anotar_frame_com_ids(amostra.frame_bgr, amostra.pessoas)
    img_b64 = frame_para_base64(frame_anotado)

    contexto_partes = []
    for p in amostra.pessoas:
        if p["zona"]:
            contexto_partes.append(f"{p['rotulo']} está em {p['zona']}")
    contexto = ". ".join(contexto_partes) if contexto_partes else "sem zonas pré-definidas"

    prompt = PROMPT_VLM.format(
        bloco_processo=construir_bloco_processo(descricao_processo),
        bloco_vocabulario=construir_bloco_vocabulario(memoria),
        contexto_zonas=contexto,
    )

    try:
        resposta = groq_vision_call(
            groq_client, img_b64, prompt, json_mode=True, max_tokens=600
        )
        dados = json.loads(resposta)
        acoes = dados.get("acoes", {})
    except json.JSONDecodeError:
        log.warning(f"JSON inválido no frame {amostra.frame_idx}")
        return {}
    except Exception as e:
        log.warning(f"Falha na análise do frame {amostra.frame_idx}: {e}")
        return {}

    mapa_rotulo_tid = {p["rotulo"]: p["track_id"] for p in amostra.pessoas}
    return {
        mapa_rotulo_tid[rot]: desc.strip().lower()
        for rot, desc in acoes.items()
        if rot in mapa_rotulo_tid and isinstance(desc, str) and desc.strip()
    }


def etapa_analise_vlm(
    groq_client: Groq,
    amostras: list[Amostra],
    descricao_processo: str,
    memoria: dict,
    progress_cb: ProgressCb,
) -> list[dict]:
    progress_cb("vlm", 0, f"Analisando {len(amostras)} amostras com VLM")
    observacoes: list[dict] = []
    for i, am in enumerate(amostras):
        descricoes = _analisar_amostra_vlm(groq_client, am, descricao_processo, memoria)
        for p in am.pessoas:
            desc = descricoes.get(p["track_id"])
            if desc:
                observacoes.append(
                    {
                        "tempo_s": am.tempo_s,
                        "frame_idx": am.frame_idx,
                        "track_id": p["track_id"],
                        "descricao": desc,
                        "bbox": p["bbox"],
                        "zona": p["zona"],
                    }
                )
        pct = int((i + 1) / max(1, len(amostras)) * 100)
        progress_cb(
            "vlm", pct, f"{i + 1}/{len(amostras)} amostras · {len(observacoes)} observações"
        )
    return observacoes


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 3 · Clusterização
# ═════════════════════════════════════════════════════════════════════════
def etapa_clusterizar(
    groq_client: Groq,
    observacoes_brutas: list[dict],
    descricao_processo: str,
    memoria: dict,
    limiar_auto_validacao: int,
    progress_cb: ProgressCb,
) -> tuple[dict[str, str], dict[str, str], Callable[[str], str], Callable[[str], str]]:
    """Retorna (mapa_desc_label, catalogo, label_de, origem_de)."""
    progress_cb("cluster", 0, "Agrupando descrições em comportamentos")
    descricoes_unicas = sorted(set(o["descricao"] for o in observacoes_brutas))
    correcoes = memoria.get("correcoes_aprendidas", {})

    descricoes_conhecidas: dict[str, str] = {}
    descricoes_novas: list[str] = []
    for d in descricoes_unicas:
        d_lower = d.lower().strip()
        if d_lower in correcoes:
            descricoes_conhecidas[d] = correcoes[d_lower]
        else:
            descricoes_novas.append(d)

    mapa_descricao_label: dict[str, str] = {}
    catalogo: dict[str, str] = {}

    for desc, label in descricoes_conhecidas.items():
        mapa_descricao_label[desc.lower().strip()] = label
        if label not in catalogo:
            for v in memoria.get("vocabulario", []):
                if v["label"] == label:
                    catalogo[label] = v["descricao"]
                    break
            if label not in catalogo:
                catalogo[label] = label.replace("_", " ").capitalize()

    if descricoes_novas:
        prompt_completo = PROMPT_CLUSTER.format(
            bloco_processo=construir_bloco_processo(descricao_processo),
            bloco_memoria=construir_bloco_memoria_cluster(memoria),
        )
        lista_formatada = "\n".join(f"- {d}" for d in descricoes_novas)
        resposta = groq_text_call(
            groq_client,
            prompt_completo + lista_formatada,
            model=GROQ_MODEL_ANALISE,
            json_mode=True,
            max_tokens=4000,
            temperatura=0.1,
        )
        dados = json.loads(resposta)
        clusters = dados["comportamentos"]
        for c in clusters:
            for d in c.get("descricoes_originais", []):
                mapa_descricao_label[d.strip().lower()] = c["label"]
            catalogo[c["label"]] = c["descricao"]

    for d in descricoes_unicas:
        if d.lower().strip() not in mapa_descricao_label:
            log.warning(f"Descrição não clusterizada: {d!r}")
            mapa_descricao_label[d.lower().strip()] = "acao_indefinida"

    if "acao_indefinida" not in catalogo:
        catalogo["acao_indefinida"] = "Ação não foi identificada com clareza pelo modelo"

    def label_de(desc: str) -> str:
        return mapa_descricao_label.get(desc.lower().strip(), "acao_indefinida")

    vocab_estabelecido = {
        v["label"]
        for v in memoria.get("vocabulario", [])
        if v["n_confirmacoes"] >= limiar_auto_validacao
    }
    origem_por_desc: dict[str, str] = {}
    for desc in descricoes_unicas:
        d_lower = desc.lower().strip()
        if d_lower in correcoes:
            origem_por_desc[d_lower] = "correcao_aprendida"
        elif mapa_descricao_label.get(d_lower) in vocab_estabelecido:
            origem_por_desc[d_lower] = "vocabulario_canonico"
        else:
            origem_por_desc[d_lower] = "pendente"

    def origem_de(desc: str) -> str:
        return origem_por_desc.get(desc.lower().strip(), "pendente")

    progress_cb("cluster", 100, f"{len(catalogo)} comportamentos canônicos")
    return mapa_descricao_label, catalogo, label_de, origem_de


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 4 · Segmentação em eventos
# ═════════════════════════════════════════════════════════════════════════
def etapa_segmentar_eventos(
    observacoes_brutas: list[dict],
    label_de: Callable[[str], str],
    intervalo_s: float,
) -> list[dict]:
    for o in observacoes_brutas:
        o["label"] = label_de(o["descricao"])

    por_pessoa: dict[int, list[dict]] = defaultdict(list)
    for o in observacoes_brutas:
        por_pessoa[o["track_id"]].append(o)
    for tid in por_pessoa:
        por_pessoa[tid].sort(key=lambda o: o["tempo_s"])

    janela_continuidade_s = intervalo_s * 1.6
    eventos: list[dict] = []

    for tid, obs_lista in por_pessoa.items():
        if not obs_lista:
            continue
        atual = {
            "pessoa_track_id": tid,
            "comportamento_label": obs_lista[0]["label"],
            "descricao_bruta": obs_lista[0]["descricao"],
            "tempo_inicio_s": obs_lista[0]["tempo_s"],
            "tempo_fim_s": obs_lista[0]["tempo_s"],
            "frame_inicio": obs_lista[0]["frame_idx"],
            "frame_fim": obs_lista[0]["frame_idx"],
            "bbox_inicio": list(obs_lista[0]["bbox"]),
            "zona_contexto": obs_lista[0]["zona"],
            "n_amostras": 1,
        }
        for o in obs_lista[1:]:
            gap = o["tempo_s"] - atual["tempo_fim_s"]
            if o["label"] == atual["comportamento_label"] and gap <= janela_continuidade_s:
                atual["tempo_fim_s"] = o["tempo_s"]
                atual["frame_fim"] = o["frame_idx"]
                atual["n_amostras"] += 1
            else:
                eventos.append(atual)
                atual = {
                    "pessoa_track_id": tid,
                    "comportamento_label": o["label"],
                    "descricao_bruta": o["descricao"],
                    "tempo_inicio_s": o["tempo_s"],
                    "tempo_fim_s": o["tempo_s"],
                    "frame_inicio": o["frame_idx"],
                    "frame_fim": o["frame_idx"],
                    "bbox_inicio": list(o["bbox"]),
                    "zona_contexto": o["zona"],
                    "n_amostras": 1,
                }
        eventos.append(atual)

    for e in eventos:
        e["tempo_fim_s"] = round(e["tempo_fim_s"] + intervalo_s, 2)
        e["tempo_inicio_s"] = round(e["tempo_inicio_s"], 2)
        e["confianca"] = round(min(0.95, 0.6 + 0.05 * e["n_amostras"]), 2)

    return eventos


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 5 · Persistência
# ═════════════════════════════════════════════════════════════════════════
def etapa_persistir(
    sb: Client,
    empresa: str,
    processo: str,
    video_path: str,
    info_video: dict,
    eventos: list[dict],
    ids_unicos: list[int],
    catalogo: dict[str, str],
    origem_de: Callable[[str], str],
) -> tuple[str, int]:
    """Persiste vídeo, comportamentos, eventos. Retorna (video_id, n_auto_validados)."""
    video_row = (
        sb.table("videos")
        .insert(
            {
                "empresa": empresa,
                "processo": processo,
                "nome": Path(video_path).name,
                "caminho": str(video_path),
                "duracao_s": round(info_video["duracao_s"], 2),
                "fps": round(info_video["fps"], 2),
                "largura": info_video["largura"],
                "altura": info_video["altura"],
                "total_pessoas": len(ids_unicos),
                "total_eventos": len(eventos),
            }
        )
        .execute()
    )
    video_id = video_row.data[0]["id"]

    por_label = Counter(e["comportamento_label"] for e in eventos)
    for label, descricao in catalogo.items():
        n_neste_video = por_label.get(label, 0)
        if n_neste_video == 0:
            continue
        existente = (
            sb.table("comportamentos")
            .select("id, total_ocorrencias")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .eq("label", label)
            .execute()
        )
        if existente.data:
            atual = existente.data[0]
            sb.table("comportamentos").update(
                {
                    "total_ocorrencias": atual["total_ocorrencias"] + n_neste_video,
                    "ultima_observacao": datetime.utcnow().isoformat(),
                }
            ).eq("id", atual["id"]).execute()
        else:
            sb.table("comportamentos").insert(
                {
                    "empresa": empresa,
                    "processo": processo,
                    "label": label,
                    "descricao": descricao,
                    "total_ocorrencias": n_neste_video,
                }
            ).execute()

    linhas_eventos: list[dict] = []
    n_auto_validados = 0
    for e in eventos:
        origem = origem_de(e["descricao_bruta"])
        auto_validado = origem in ("correcao_aprendida", "vocabulario_canonico")
        row = {
            "video_id": video_id,
            "empresa": empresa,
            "processo": processo,
            "pessoa_track_id": e["pessoa_track_id"],
            "comportamento_label": e["comportamento_label"],
            "descricao_bruta": e["descricao_bruta"],
            "tempo_inicio_s": e["tempo_inicio_s"],
            "tempo_fim_s": e["tempo_fim_s"],
            "frame_inicio": e["frame_inicio"],
            "frame_fim": e["frame_fim"],
            "bbox_inicio": {
                "x1": e["bbox_inicio"][0],
                "y1": e["bbox_inicio"][1],
                "x2": e["bbox_inicio"][2],
                "y2": e["bbox_inicio"][3],
            },
            "zona_contexto": e["zona_contexto"],
            "n_amostras": e["n_amostras"],
            "confianca": e["confianca"],
            "origem_validacao": origem,
            # IMPORTANTE: validado_humano sempre explícito (PostgREST batch
            # não aplica DEFAULT de coluna ausente).
            "validado_humano": auto_validado,
        }
        if auto_validado:
            row["validacao_correto"] = True
            row["validado_em"] = datetime.utcnow().isoformat()
            n_auto_validados += 1
        linhas_eventos.append(row)

    CHUNK = 100
    for i in range(0, len(linhas_eventos), CHUNK):
        sb.table("eventos").insert(linhas_eventos[i : i + CHUNK]).execute()

    return video_id, n_auto_validados


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 6 · Análise de produtividade (sugestões agregadas)
# ═════════════════════════════════════════════════════════════════════════
PROMPT_ANALISE = """Você é um consultor sênior de produtividade industrial e engenharia de processos, especialista em Lean Manufacturing.

Você está analisando dados REAIS e AGREGADOS de múltiplos turnos de operação da empresa "{empresa}", no processo "{processo}". Os dados vêm de visão computacional sobre vídeos da operação.

PERGUNTA CENTRAL que você deve responder com suas sugestões:
"Onde podemos melhorar este processo para AUMENTAR A PRODUTIVIDADE, considerando o contexto empresarial e processual disponível?"

Gere de 3 a 6 SUGESTÕES priorizadas por IMPACTO em produtividade. Para cada uma:
- prioridade: "alta" | "media" | "info"  (alta = maior ganho de produtividade)
- area: termo curto (ex: "Gargalo", "Ociosidade", "Fluxo", "Balanceamento", "Layout", "Retrabalho", "Movimentação")
- situacao: o que os DADOS AGREGADOS mostram, com números reais (tempos, %, frequência, nº de vídeos) — 1 a 2 frases
- causa_provavel: hipótese de causa-raiz da perda de produtividade (1-2 frases)
- sugestao: ação concreta e acionável que aumenta a produtividade (1-2 frases)
- impacto_estimado: estimativa do ganho potencial em produtividade, qualitativa ou quantitativa (ex: "redução de ~15% no tempo ocioso", "ganho médio-alto") — seja realista
- comportamentos_relacionados: lista dos labels envolvidos

COMO PENSAR (Lean — 7 desperdícios): espera/ociosidade, transporte e movimentação desnecessária, processamento excessivo, retrabalho/defeitos, estoque/fila, superprodução. Procure onde o TEMPO está sendo gasto sem agregar valor.

REGRAS:
- Baseie-se na BASE AGREGADA (vários turnos), não num único vídeo. Padrões recorrentes em muitos vídeos são mais confiáveis que achados de um vídeo só.
- Priorize comportamentos que consomem MUITO % do tempo observado — é onde mora o maior ganho.
- Quantifique sempre que possível, usando os números do contexto.
- NUNCA mencione pessoas específicas (track_ids). Fale em termos de processo e estação.
- Se houver "descricao_processo_pelo_cliente", use-a como referência do fluxo que AGREGA valor: tempo gasto fora desse fluxo é candidato a desperdício.
- Se houver "labels_descartados_pelo_cliente", ignore esses comportamentos (são falsos positivos).
- Se a base ainda for pequena (poucos vídeos / pouco tempo observado), seja mais cauteloso e sinalize isso no impacto_estimado.
- Seja específico e acionável. Evite frases genéricas como "melhorar o fluxo".

Responda APENAS um JSON no formato:
{{"sugestoes": [{{"prioridade": "...", "area": "...", "situacao": "...", "causa_provavel": "...", "sugestao": "...", "impacto_estimado": "...", "comportamentos_relacionados": [...]}}, ...]}}

DADOS AGREGADOS DO PROCESSO:
"""


def _label_efetivo(e: dict) -> str:
    return e.get("label_corrigido") or e.get("comportamento_label")


def montar_contexto_agregado(
    sb: Client,
    empresa: str,
    processo: str,
    catalogo: dict[str, str] | None = None,
    descricao_processo: str = "",
    memoria: dict | None = None,
    video_recem_processado: dict | None = None,
) -> dict:
    todos_eventos = (
        sb.table("eventos")
        .select(
            "video_id, comportamento_label, label_corrigido, tempo_inicio_s, "
            "tempo_fim_s, pessoa_track_id, validacao_correto, validado_humano"
        )
        .eq("empresa", empresa)
        .eq("processo", processo)
        .limit(50000)
        .execute()
        .data
    )
    base = [e for e in todos_eventos if e.get("validacao_correto") is not False]

    videos_ctx = (
        sb.table("videos")
        .select("id, duracao_s, processado_em")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .execute()
        .data
    )
    n_videos_ctx = len(videos_ctx)
    duracao_total_ctx = sum((v.get("duracao_s") or 0) for v in videos_ctx)

    catalogo = catalogo or {}
    if not catalogo:
        comps = (
            sb.table("comportamentos")
            .select("label, descricao")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .execute()
            .data
        )
        catalogo = {c["label"]: c.get("descricao", "") for c in comps}

    agg: dict = defaultdict(
        lambda: {"ocorrencias": 0, "duracao_total_s": 0.0, "videos": set(), "pessoas": set()}
    )
    for e in base:
        lbl = _label_efetivo(e)
        dur = (e.get("tempo_fim_s") or 0) - (e.get("tempo_inicio_s") or 0)
        a = agg[lbl]
        a["ocorrencias"] += 1
        a["duracao_total_s"] += max(0, dur)
        a["videos"].add(e.get("video_id"))
        a["pessoas"].add(e.get("pessoa_track_id"))

    resumo_agregado = []
    for lbl, a in sorted(agg.items(), key=lambda kv: kv[1]["duracao_total_s"], reverse=True):
        resumo_agregado.append(
            {
                "comportamento": lbl,
                "descricao": catalogo.get(lbl, lbl),
                "ocorrencias_totais": a["ocorrencias"],
                "duracao_total_s": round(a["duracao_total_s"], 1),
                "duracao_media_s": round(a["duracao_total_s"] / max(1, a["ocorrencias"]), 1),
                "pct_do_tempo_observado": round(
                    a["duracao_total_s"] / max(1, duracao_total_ctx) * 100, 1
                ),
                "aparece_em_n_videos": len(a["videos"]),
            }
        )

    seqs: dict = defaultdict(list)
    for e in base:
        seqs[(e.get("video_id"), e.get("pessoa_track_id"))].append(
            (e.get("tempo_inicio_s") or 0, _label_efetivo(e))
        )
    contagem_transicoes: Counter = Counter()
    for _, lista in seqs.items():
        lista.sort()
        labels = [l for _, l in lista]
        for x, y in zip(labels, labels[1:]):
            if x != y:
                contagem_transicoes[(x, y)] += 1
    transicoes_top = contagem_transicoes.most_common(12)

    n_validados = sum(1 for e in base if e.get("validado_humano"))
    pct_validado = round(n_validados / max(1, len(base)) * 100, 1)

    contexto: dict = {
        "empresa": empresa,
        "processo": processo,
        "base_agregada": {
            "videos_analisados": n_videos_ctx,
            "tempo_total_observado_s": round(duracao_total_ctx, 1),
            "tempo_total_observado_min": round(duracao_total_ctx / 60, 1),
            "eventos_considerados": len(base),
            "pct_base_validada_por_humano": pct_validado,
        },
        "distribuicao_comportamentos": resumo_agregado,
        "transicoes_dominantes": [
            {"de": a, "para": b, "vezes": n} for (a, b), n in transicoes_top
        ],
    }
    if video_recem_processado:
        contexto["video_recem_processado"] = video_recem_processado
    if descricao_processo:
        contexto["descricao_processo_pelo_cliente"] = descricao_processo
    if memoria and memoria.get("descartados"):
        contexto["labels_descartados_pelo_cliente"] = memoria["descartados"]

    return contexto


def etapa_gerar_sugestoes(
    sb: Client,
    groq_client: Groq,
    empresa: str,
    processo: str,
    video_id: str,
    contexto_analise: dict,
) -> list[dict]:
    prompt = PROMPT_ANALISE.format(empresa=empresa, processo=processo)
    resposta = groq_text_call(
        groq_client,
        prompt + json.dumps(contexto_analise, indent=2, ensure_ascii=False),
        model=GROQ_MODEL_ANALISE,
        json_mode=True,
        max_tokens=4000,
        temperatura=0.3,
    )
    sugestoes = json.loads(resposta)["sugestoes"]

    linhas_sug = []
    for s in sugestoes:
        linhas_sug.append(
            {
                "video_id": video_id,
                "empresa": empresa,
                "processo": processo,
                "prioridade": s.get("prioridade", "info"),
                "area": s.get("area", ""),
                "situacao": s.get("situacao", ""),
                "causa_provavel": s.get("causa_provavel", ""),
                "sugestao": s.get("sugestao", ""),
                "impacto_estimado": s.get("impacto_estimado", ""),
                "eventos_relacionados": {
                    "comportamentos": s.get("comportamentos_relacionados", [])
                },
            }
        )
    if linhas_sug:
        sb.table("sugestoes_melhoria").insert(linhas_sug).execute()
    return sugestoes


# ═════════════════════════════════════════════════════════════════════════
# FRAMES PARA VALIDAÇÃO
# ═════════════════════════════════════════════════════════════════════════
def extrair_3_frames_evento(evento: dict, video_path: str) -> list[np.ndarray]:
    fi, ff = evento["frame_inicio"], evento["frame_fim"]
    fmid = (fi + ff) // 2
    frames_alvo = [fi, fmid, ff] if fi != ff else [fi]

    cap = cv2.VideoCapture(video_path)
    crops: list[np.ndarray] = []
    bbox = evento["bbox_inicio"]
    if isinstance(bbox, dict):
        x1, y1, x2, y2 = int(bbox["x1"]), int(bbox["y1"]), int(bbox["x2"]), int(bbox["y2"])
    else:
        x1, y1, x2, y2 = (int(v) for v in bbox)
    for f_idx in frames_alvo:
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, frame = cap.read()
        if not ret:
            continue
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 100), 4)
        cv2.putText(
            frame,
            f'P{evento["pessoa_track_id"]:03d}',
            (x1, max(20, y1 - 10)),
            cv2.FONT_HERSHEY_DUPLEX,
            1.0,
            (0, 255, 100),
            2,
        )
        h, w = frame.shape[:2]
        escala = 480 / max(h, w)
        frame_small = cv2.resize(frame, (int(w * escala), int(h * escala)))
        crops.append(frame_small)
    cap.release()
    return crops


def frame_para_jpeg_bytes(frame_bgr: np.ndarray, qualidade: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
    assert ok
    return buf.tobytes()


# ═════════════════════════════════════════════════════════════════════════
# CHAT
# ═════════════════════════════════════════════════════════════════════════
def montar_snapshot_chat(sb: Client, empresa: str, processo: str) -> dict:
    evs = (
        sb.table("eventos")
        .select(
            "video_id, comportamento_label, label_corrigido, tempo_inicio_s, "
            "tempo_fim_s, validacao_correto, validado_humano"
        )
        .eq("empresa", empresa)
        .eq("processo", processo)
        .limit(50000)
        .execute()
        .data
    )
    base = [e for e in evs if e.get("validacao_correto") is not False]

    vids = (
        sb.table("videos")
        .select("id, duracao_s")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .execute()
        .data
    )
    dur_total = sum((v.get("duracao_s") or 0) for v in vids)

    comps = (
        sb.table("comportamentos")
        .select("label, descricao")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .execute()
        .data
    )
    desc_por_label = {c["label"]: c.get("descricao", "") for c in comps}

    agg: dict = {}
    for e in base:
        l = _label_efetivo(e)
        d = max(0, (e.get("tempo_fim_s") or 0) - (e.get("tempo_inicio_s") or 0))
        a = agg.setdefault(l, {"oc": 0, "dur": 0.0, "vids": set()})
        a["oc"] += 1
        a["dur"] += d
        a["vids"].add(e.get("video_id"))

    distrib = []
    for l, a in sorted(agg.items(), key=lambda kv: kv[1]["dur"], reverse=True):
        distrib.append(
            {
                "comportamento": l,
                "descricao": desc_por_label.get(l, l),
                "ocorrencias": a["oc"],
                "tempo_total_s": round(a["dur"], 1),
                "pct_tempo": round(a["dur"] / max(1, dur_total) * 100, 1),
                "em_n_videos": len(a["vids"]),
            }
        )

    sugs = (
        sb.table("sugestoes_melhoria")
        .select("prioridade, area, situacao, sugestao, impacto_estimado")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .order("criado_em", desc=True)
        .limit(8)
        .execute()
        .data
    )

    n_val = sum(1 for e in base if e.get("validado_humano"))
    return {
        "videos_analisados": len(vids),
        "tempo_total_observado_min": round(dur_total / 60, 1),
        "eventos_considerados": len(base),
        "pct_validado_por_humano": round(n_val / max(1, len(base)) * 100, 1),
        "distribuicao_comportamentos": distrib,
        "sugestoes_recentes": sugs,
    }


def system_prompt_chat(empresa: str, processo: str, descricao_processo: str, snapshot: dict) -> str:
    partes = [
        "Você é um consultor sênior de produtividade industrial e engenharia de processos, especialista em Lean Manufacturing.",
        f'Está ajudando a empresa "{empresa}" a melhorar o processo "{processo}".',
        "",
        "Você tem acesso a dados reais coletados por visão computacional sobre a operação (abaixo, em JSON). Use-os para embasar suas respostas com números concretos.",
    ]
    if descricao_processo:
        partes += ["", "DESCRIÇÃO DO PROCESSO (fornecida pelo cliente):", descricao_processo]
    partes += [
        "",
        "DADOS AGREGADOS DA OPERAÇÃO (JSON):",
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        "",
        "COMO RESPONDER:",
        "- Quando a pergunta envolver o que acontece na operação (tempos, %, comportamentos, gargalos), baseie-se nos DADOS e cite os números reais.",
        "- Combine os dados com BOAS PRÁTICAS DE MERCADO (Lean, redução dos 7 desperdícios, balanceamento de linha, 5S, padronização, ergonomia, etc.).",
        "- Foque sempre em PRODUTIVIDADE: onde há tempo sem valor agregado e como recuperá-lo.",
        "- Seja específico, prático e acionável. Evite generalidades vazias.",
        "- Se os dados não cobrirem a pergunta, diga isso com transparência e responda com recomendação geral de mercado, deixando claro que é uma orientação (não uma leitura dos dados).",
        "- NUNCA invente números que não estejam nos dados.",
        "- Não comente desempenho de pessoas/indivíduos; fale sempre em termos de processo e estações.",
        "- Responda em português do Brasil, de forma clara e organizada. Use listas curtas quando ajudar.",
    ]
    return "\n".join(partes)


def responder_chat(
    groq_client: Groq,
    sb: Client,
    empresa: str,
    processo: str,
    pergunta: str,
    historico: list[dict] | None = None,
    max_trocas: int = 6,
) -> str:
    historico = historico or []
    descricao = resolver_descricao_processo(sb, empresa, processo, None)
    snapshot = montar_snapshot_chat(sb, empresa, processo)

    mensagens = [
        {"role": "system", "content": system_prompt_chat(empresa, processo, descricao, snapshot)}
    ]
    mensagens += historico[-max_trocas * 2 :]
    mensagens.append({"role": "user", "content": pergunta})

    r = groq_client.chat.completions.create(
        model=GROQ_MODEL_ANALISE,
        messages=mensagens,
        temperature=0.4,
        max_completion_tokens=1500,
    )
    return r.choices[0].message.content


# ═════════════════════════════════════════════════════════════════════════
# ORQUESTRADOR — chamada principal do worker
# ═════════════════════════════════════════════════════════════════════════
def processar_video(
    empresa: str,
    processo: str,
    video_path: str,
    descricao_processo: str | None = None,
    intervalo_amostragem_s: float = DEFAULT_INTERVALO_AMOSTRAGEM_S,
    limiar_auto_validacao: int = DEFAULT_LIMIAR_AUTO_VALIDACAO,
    rois_contexto: dict | None = None,
    progress_cb: ProgressCb | None = None,
    sb: Client | None = None,
    groq_client: Groq | None = None,
    yolo_model: YOLO | None = None,
) -> dict:
    """Roda o pipeline completo. Devolve dict com video_id, n_eventos,
    n_auto_validados, n_sugestoes.
    """
    progress_cb = progress_cb or _noop_progress
    rois_contexto = rois_contexto or DEFAULT_ROIS_CONTEXTO
    sb = sb or make_supabase_client()
    groq_client = groq_client or make_groq_client()
    yolo = yolo_model or YOLO(YOLO_MODEL)

    progress_cb("setup", 0, f"Iniciando · {empresa}/{processo}")
    memoria = carregar_memoria_do_negocio(sb, empresa, processo)
    descricao = resolver_descricao_processo(sb, empresa, processo, descricao_processo)
    progress_cb(
        "setup",
        100,
        f"Memória: {memoria['total_eventos_validados']} eventos validados",
    )

    amostras, info_video, ids_unicos = etapa_detectar_e_amostrar(
        yolo, video_path, intervalo_amostragem_s, rois_contexto, progress_cb
    )

    if not amostras:
        progress_cb("concluido", 100, "Nenhuma pessoa detectada no vídeo")
        return {
            "video_id": None,
            "n_eventos": 0,
            "n_auto_validados": 0,
            "n_sugestoes": 0,
        }

    observacoes = etapa_analise_vlm(groq_client, amostras, descricao, memoria, progress_cb)

    if not observacoes:
        progress_cb("concluido", 100, "Nenhuma observação obtida do VLM")
        return {
            "video_id": None,
            "n_eventos": 0,
            "n_auto_validados": 0,
            "n_sugestoes": 0,
        }

    _, catalogo, label_de, origem_de = etapa_clusterizar(
        groq_client, observacoes, descricao, memoria, limiar_auto_validacao, progress_cb
    )

    progress_cb("segmentar", 0, "Formando eventos contínuos")
    eventos = etapa_segmentar_eventos(observacoes, label_de, intervalo_amostragem_s)
    progress_cb("segmentar", 100, f"{len(eventos)} eventos formados")

    progress_cb("persistir", 0, "Salvando no banco de dados")
    video_id, n_auto = etapa_persistir(
        sb, empresa, processo, video_path, info_video, eventos, ids_unicos, catalogo, origem_de
    )
    progress_cb("persistir", 100, f"{len(eventos)} eventos · {n_auto} auto-validados")

    progress_cb("sugestoes", 0, "Gerando sugestões de produtividade")
    contexto = montar_contexto_agregado(
        sb,
        empresa,
        processo,
        catalogo=catalogo,
        descricao_processo=descricao,
        memoria=memoria,
        video_recem_processado={
            "nome": Path(video_path).name,
            "duracao_s": round(info_video["duracao_s"], 1),
            "eventos_neste_video": len(eventos),
        },
    )
    sugestoes = etapa_gerar_sugestoes(sb, groq_client, empresa, processo, video_id, contexto)
    progress_cb("sugestoes", 100, f"{len(sugestoes)} sugestões geradas")

    progress_cb("concluido", 100, "Processamento concluído")
    return {
        "video_id": video_id,
        "n_eventos": len(eventos),
        "n_auto_validados": n_auto,
        "n_sugestoes": len(sugestoes),
    }
