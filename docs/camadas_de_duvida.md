# Camadas de dúvida — como escrever uma regra

Verificações determinísticas que confrontam o rótulo da IA com o que a cena
mostra. Quando contradizem, o evento **não é corrigido — é marcado como dúvida**
e vai para a fila. A máquina não sabe qual lado está certo; quem sabe é você.

Rodam em CPU. **Nenhuma camada gera chamada extra ao VLM.**

As camadas são **dados, não código**: você escreve a décima regra pela API, sem
deploy. Vale a partir do próximo processamento.

---

## Os sinais disponíveis

| Sinal | Tipo | O que é |
|---|---|---|
| `pessoas_na_cena` | número | pessoas distintas detectadas no minuto |
| `pessoas_no_posto` | número | quantas estão na zona do posto do operador |
| `zonas_ocupadas` | lista | nomes das zonas com alguém dentro |
| `maos_na_maquina` | sim/não | punho dentro da zona da máquina |
| `movimento` | `"parado"` \| `"andando"` | trajetória no minuto |
| `deslocamento_rel` | número | alturas-de-corpo por segundo (o número cru) |
| `concordancia` | 0 a 1 | quanto as amostras do minuto concordaram |
| `n_rotulos_no_minuto` | número | quantos rótulos disputaram o minuto |
| `duracao_s` | número | duração do evento |

> **Sinal ausente nunca vira dúvida.** Se o processo não tem zona de máquina
> desenhada, `maos_na_maquina` não existe e toda regra que fala dele fica
> quieta. Falta de dado não é suspeita.

---

## Formato

```json
{
  "nome": "interacao_sem_segunda_pessoa",
  "quando_rotulo": ["conversar_com_colega", "conversar_com_colega_ou_lider",
                    "receber_instrucao", "orientar_colega"],
  "se": { "pessoas_na_cena": { "<=": 1 } },
  "motivo": "O rótulo implica interação entre pessoas, mas só uma foi detectada na cena.",
  "modo": "sombra",
  "ordem": 10
}
```

```json
{
  "nome": "operar_sem_mao_na_maquina",
  "quando_rotulo": ["operar_torno", "operar_maquina_industrial", "ajustar_maquina"],
  "se": {
    "e": [
      { "maos_na_maquina": { "==": false } },
      { "concordancia": { "<": 0.7 } }
    ]
  },
  "motivo": "O rótulo implica operar a máquina, mas nenhuma mão foi detectada nela e as amostras do minuto discordam.",
  "modo": "sombra",
  "ordem": 20
}
```

### Regras do formato

- **`quando_rotulo` é sempre lista.** `["*"]` vale para todos os rótulos.
- **`se`** aceita um objeto simples (E implícito entre as chaves) ou os
  combinadores `e` / `ou` / `nao`, aninháveis à vontade.
- **Operadores:** `==` `!=` `<` `<=` `>` `>=` `em` `contem`.
  Açúcar: `{"maos_na_maquina": false}` equivale a `{"maos_na_maquina": {"==": false}}`.
- **A camada nunca corrige o rótulo.** Só marca dúvida.
- **`motivo`** é o texto que o validador lê para saber por que aquilo caiu na
  fila. Sem ele, o validador está adivinhando junto com a máquina.
- **`ordem`** só define a sequência de avaliação (menor primeiro).

---

## Modo sombra — meça antes de ligar

| modo | dispara? | marca dúvida? | entra no placar? |
|---|---|---|---|
| `sombra` | sim | **não** | **sim** |
| `ativa` | sim | sim | sim |
| `off` | não | não | não |

**Toda regra nova nasce em `sombra`** (é o default da API, de propósito). Ela
roda, conta quantas vezes dispararia e quantos minutos colocaria em dúvida —
sem tocar em nada. Você olha o placar e decide se liga.

Sem isso, adicionar camada seria aposta.

---

## Placar por camada

```
GET /processos/{id}/camadas/placar
```

| Campo | O que diz |
|---|---|
| `disparos` | quantas vezes a camada apontou |
| `minutos_em_duvida` | quanto tempo ela colocou em dúvida |
| `validados` | quantos desses já foram julgados por um humano |
| `acertos` | o humano **mudou** o rótulo ou descartou o evento |
| `falsos_alarmes` | o humano **confirmou** o rótulo original |
| `taxa_acerto` | acertos ÷ validados (`null` enquanto ninguém validou) |

**Como ler:** camada que dispara muito e tem taxa de acerto baixa está gerando
trabalho sem gerar informação — desligue com evidência, não por intuição. Uma
taxa alta significa que ela está achando erro real e merece continuar.

`taxa_acerto` é `null` até alguém validar. Não inventamos taxa sem amostra.

---

## API

```bash
# criar/atualizar (nasce em sombra)
PUT    /processos/{id}/camadas/{nome}
# listar
GET    /processos/{id}/camadas
# placar
GET    /processos/{id}/camadas/placar
# remover
DELETE /processos/{id}/camadas/{nome}
```

Exemplo, no console do navegador já logado:

```js
await fetch(`${API}/processos/${procId}/camadas/interacao_sem_segunda_pessoa`, {
  method: 'PUT', headers: H,
  body: JSON.stringify({
    nome: "interacao_sem_segunda_pessoa",
    quando_rotulo: ["conversar_com_colega", "conversar_com_colega_ou_lider"],
    se: { pessoas_na_cena: { "<=": 1 } },
    motivo: "O rótulo implica interação, mas só uma pessoa foi detectada.",
    modo: "sombra"
  })
});
```

---

## Calibrar `parado` / `andando`

`deslocamento_rel` é medido em **alturas-de-corpo por segundo**, não em pixels.
Isso é deliberado: pixel depende da resolução e da distância da câmera, então o
mesmo operador andando daria números diferentes em cada câmera e a décima regra
viraria adivinhação. Em alturas-de-corpo o número é comparável entre câmeras e
praticamente dispensa calibração.

Default: `KV_MOV_LIMIAR=0.15` (≈ um sexto de corpo por segundo).

**Como calibrar com os dados que já existem**, sem chutar: rode um vídeo já
processado e compare a distribuição de `deslocamento_rel` entre eventos de
rótulos claramente parados (`operar_torno`, `monitorar_maquina`) e claramente
móveis (`andar`, `buscar_material`). O limiar certo é o vale entre as duas
distribuições. Se elas se sobrepõem demais, o sinal não separa nesse processo —
e aí é melhor não escrever regra sobre movimento do que escrever uma ruim.
