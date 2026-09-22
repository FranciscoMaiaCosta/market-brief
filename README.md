# market-brief

Preços da watchlist do Morning Brief. Todos os dias às 05:00 UTC (06:00 em Lisboa no verão), um GitHub Action corre `prices.py` e atualiza:

- `out/watchlist.html`: a secção Watchlist, pronta a colar no brief
- `out/watchlist.json`: os mesmos números, com datas e fontes

O prompt agendado no Claude lê estes ficheiros no início de cada brief. Aqui não há IA, só o script.

## Editar a watchlist

Edita `watchlist.txt`, uma linha por instrumento. O formato está explicado no topo do ficheiro. A alteração entra na corrida do dia seguinte.

## Correr à mão

- No GitHub: Actions → prices → Run workflow
- Localmente: `python prices.py` (no Windows é preciso `pip install tzdata`)

## Fontes e regras

Yahoo Finance, CNBC e FRED, todos sem chave. Cada variação é o último fecho completo face ao anterior, e cada fecho tem a sua data. O que não se consegue obter fica n/d; nada é estimado. Se menos de metade das linhas vier preenchida, o script não substitui os ficheiros e a corrida falha, para o GitHub enviar um email a avisar.
