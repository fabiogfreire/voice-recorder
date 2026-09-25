# SPEC — Silenciar automaticamente outros apps ao iniciar uma call (25/09/2026)

## Contexto

Na call do Teams de 25/09, um vídeo do YouTube que tocava no início ficou gravado
junto com a reunião (ver transcrição da gravação 35: as primeiras linhas são sobre
voo executivo/IFR, tema do vídeo, não da call). A causa: o loopback grava tudo que
sai do alto-falante, sem distinguir aplicativo.

## Caminho investigado e descartado: captura por processo

Testamos `ActivateAudioInterfaceAsync` com `AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`
(API nativa do Windows, build mínima 19041 — a máquina do Fabio roda build 26200) para
gravar só o áudio do `ms-teams.exe`, ignorando qualquer outro processo.

**Funciona perfeitamente para apps comuns:** validado com dois processos PowerShell
de teste — RMS alto no processo que tocava som, RMS zero no processo silencioso,
mesmo com os dois rodando ao mesmo tempo.

**Não funciona para o Teams:** 4 tentativas reais (chamada de teste de áudio, reunião
sozinho com vídeo compartilhado, reunião com voz ao vivo) deram RMS = 0.0000 do
início ao fim, apesar da ativação suceder normalmente (`hr=0`, formato aceito,
buffers do tamanho certo chegando). Confirmado por busca: esse comportamento é
documentado — o próprio exemplo oficial da Microsoft (`ApplicationLoopback` sample)
e o recurso de captura por app do OBS reproduzem o mesmo silêncio com Teams. A leitura
mais provável é que seja proposital: impedir que um processo capture o áudio de uma
chamada de outro processo sem consentimento explícito.

**Conclusão:** isolar o áudio do Teams por processo não é viável via API pública do
Windows. Não adianta insistir nesse caminho.

## Abordagem escolhida: silenciar a origem, não filtrar o resultado

Em vez de tentar separar as fontes depois de gravadas, evitar que a fonte
indesejada (YouTube, Spotify, etc.) toque durante a call.

**API:** `IAudioSessionManager2` / `IAudioSessionControl2` (via `IMMDevice::Activate`
no dispositivo de saída padrão) — infraestrutura padrão de "sessões de áudio" do
Windows (é a mesma coisa que o mixer de volume do Windows usa para mostrar um
controle de volume por app). Sem a restrição de VoIP que bloqueou o process loopback,
porque aqui não estamos tentando capturar o Teams — estamos mutando *outro* app.

## Fluxo

1. `MicWatcher.on_call_start()` já dispara hoje quando detecta uso do microfone
   (`main.py:106`). Nesse ponto, antes de iniciar `CallRecording`:
2. Enumerar as sessões de áudio ativas no dispositivo de saída padrão via
   `IAudioSessionEnumerator` (obtido do `IAudioSessionManager2`).
3. Para cada sessão, ler o PID do processo dono (`IAudioSessionControl2::GetProcessId`)
   e resolver o nome do executável (mesmo padrão de `QueryFullProcessImageNameW` já
   usado em `mic_watcher.py` para excluir o próprio processo).
4. **Excluir da lista:** o próprio processo do voice-recorder, e o processo que
   disparou a detecção de call (Teams/WhatsApp/etc — resolvido pelo `MicWatcher` via
   `_own_process_label()`-like lookup, adaptado para achar o app da call em vez do
   próprio app).
5. Para as sessões restantes com `IAudioMeterInformation::GetPeakValue() > 0` (ou seja,
   tocando som de verdade agora, não só abertas em segundo plano): mutar via
   `IAudioSessionControl2::QueryInterface(ISimpleAudioVolume)` +
   `ISimpleAudioVolume::SetMute(True)`.
6. Guardar a lista de (PID, estava mutado antes?) para desfazer exatamente o que foi
   mudado — nunca mutar algo que já estava mudo, e nunca deixar mutado ao restaurar.
7. `on_call_end()` (`main.py`): desmutar as sessões salvas no passo 6, na mesma ordem.

## Decisões de produto (confirmadas com o Fabio em 25/09/2026)

- **Silencioso, sem avisar.** Muta direto ao detectar a call, sem notificação —
  resolve o problema sem fricção; o app só volta a tocar quando a call encerra.
- **Escopo amplo: qualquer processo tocando som**, não uma lista fixa de apps de
  mídia. Mais robusto a apps novos, sem precisar manter lista. Risco aceito: um
  alerta sonoro de outro app durante a call também seria silenciado — nenhum caso
  assim reportado até agora.
- **Falha ao restaurar:** se o app fechar durante a call (ex: crash do Chrome), a
  sessão de áudio deixa de existir — não há dano permanente possível (mute de sessão
  não sobrevive ao processo), então não precisa de tratamento especial, só não
  quebrar se a sessão salva já não existir mais na hora de desmutar.

## Fora de escopo

- Diferenciar múltiplos participantes da call entre si (diarização) — spec separada.
- Qualquer forma de capturar o áudio do Teams isoladamente — descartado, ver acima.

## Teste de aceite

1. Tocar um vídeo do YouTube.
2. Entrar numa call (Teams/WhatsApp).
3. Confirmar que o YouTube muta sozinho no momento em que a call é detectada.
4. Encerrar a call.
5. Confirmar que o YouTube volta a tocar com som automaticamente.
6. Repetir com dois apps tocando ao mesmo tempo (ex: YouTube + Spotify) para
   confirmar que ambos são mutados e ambos restaurados.
