# market-brief

Dados do Morning Brief. Todos os dias às 05:00 UTC (06:00 em Lisboa no verão), um GitHub Action corre `prices.py` e `headlines.py` e atualiza:

- `out/watchlist.html`: a secção Watchlist, pronta a colar no brief
- `out/watchlist.json`: os mesmos números, com datas e fontes
- `out/headlines.txt`: os títulos da janela, por fonte e com hora de Lisboa
- `out/headlines.json`: os mesmos títulos com data, link e feeds sem resposta

O prompt agendado no Claude lê estes ficheiros no início de cada brief. Aqui não há IA, só o script.

## Editar a watchlist e os feeds

Edita `watchlist.txt` (um instrumento por linha) ou `feeds.txt` (um feed por linha). O formato está explicado no topo de cada ficheiro. A alteração entra na corrida do dia seguinte.

## Correr à mão

- No GitHub: Actions → prices → Run workflow
- Localmente: `python prices.py` e `python headlines.py` (no Windows é preciso `pip install tzdata`)

## Fontes e regras

Preços: Yahoo Finance, CNBC e FRED. Títulos: os feeds RSS das próprias fontes e pesquisas do Google News. Tudo sem chave. Cada variação é o último fecho completo face ao anterior, e cada fecho tem a sua data. O que não se consegue obter fica n/d; nada é estimado. Se menos de metade das linhas vier preenchida, o script não substitui os ficheiros e a corrida falha, para o GitHub enviar um email a avisar. Os títulos são leads: o brief só os publica depois de confirmar numa fonte de factos ou num documento primário.
