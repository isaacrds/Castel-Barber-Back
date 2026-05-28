import os
import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, firestore
from firebase_admin.firestore import FieldFilter
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env
load_dotenv()

# ==========================================
# 1. CONEXÃO EXCLUSIVA COM FIREBASE CASTEL BARBER VIA .ENV
# ==========================================
# O segredo aqui é o replace("\\n", "\n") para o Python entender a chave criptografada corretamente
private_key = os.getenv("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n")
client_email = os.getenv("FIREBASE_CLIENT_EMAIL", "")

cred_dict = {
    "type": "service_account",
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
    "private_key": private_key,
    "client_email": client_email,
    "client_id": os.getenv("FIREBASE_CLIENT_ID"),
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{client_email.replace('@', '%40')}",
    "universe_domain": "googleapis.com",
}

cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)

db = firestore.client()

# ==========================================
# 2. INICIALIZAÇÃO DA API
# ==========================================
app = FastAPI(title="API Exclusiva - Castel Barber")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# 3. MODELOS DE DADOS
# ==========================================
class StatusUpdate(BaseModel):
    status: str


class VerifyUpdate(BaseModel):
    phone: str
    status: str  # Pode ser: 'verify', 'negative', ou 'await'


# ==========================================
# 4. ROTAS E ENDPOINTS
# ==========================================


# ==========================================
# 🔄 ENDPOINT PARA MUDAR STATUS DO AGENDAMENTO
# ==========================================
@app.patch("/agendamentos/{id_agendamento}/status")
async def atualizar_status_agendamento(id_agendamento: str, payload: StatusUpdate):
    """
    Atualiza o status de um agendamento. Aceita: "agendado", "concluido" ou "cancelado".
    """
    status_permitidos = ["agendado", "concluido", "cancelado"]

    if payload.status not in status_permitidos:
        raise HTTPException(
            status_code=400, detail=f"Status inválido. Use: {status_permitidos}"
        )

    try:
        doc_ref = db.collection("agendamentos").document(id_agendamento)
        if not doc_ref.get().exists:
            raise HTTPException(status_code=404, detail="Agendamento não encontrado.")

        doc_ref.update({"status": payload.status})

        return {
            "status": "success",
            "message": f"Status alterado para {payload.status.upper()}",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook/verify")
async def update_user_verification(payload: VerifyUpdate):
    """
    Recebe o webhook e atualiza o Firestore da Castel Barber em Real-Time.
    """
    try:
        telefone_limpo = "".join(filter(str.isdigit, payload.phone))

        users_ref = (
            db.collection("usuarios")
            .where(filter=FieldFilter("telefone", "==", telefone_limpo))
            .stream()
        )

        user_doc = None
        for doc in users_ref:
            user_doc = doc
            break

        if not user_doc:
            raise HTTPException(
                status_code=404,
                detail="Usuário não encontrado com este telefone no banco.",
            )

        db.collection("usuarios").document(user_doc.id).update(
            {"is_verify": payload.status}
        )

        return {
            "status": "success",
            "message": f"Status de verificação alterado para '{payload.status}' com sucesso.",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 🔥 ENDPOINT DO CRON JOB (RODA A CADA 1 MINUTO)
# ==========================================
@app.get("/cron/lembretes")
async def verificar_lembretes():
    """
    Verifica se há clientes com agendamento para daqui a 1 hora, 30 minutos ou 5 minutos
    e dispara os alertas correspondentes via WhatsApp.
    """
    try:
        # 1. Pega a hora exata agora no Brasil (Fuso horário de SP)
        fuso_br = ZoneInfo("America/Sao_Paulo")
        agora = datetime.now(fuso_br)

        # Configuração dos intervalos de tempo e suas respectivas chaves de controle no Firestore
        intervalos = [
            {"minutos": 60, "campo_banco": "lembrete_1h_enviado", "label": "1 hora"},
            {"minutos": 30, "campo_banco": "lembrete_30m_enviado", "label": "30 minutos"}
            # {"minutos": 5, "campo_banco": "lembrete_5m_enviado", "label": "5 minutos"}
        ]

        enviados = 0
        total_encontrados = 0

        # Usamos httpx para fazer a chamada para a sua API principal Vortix
        async with httpx.AsyncClient() as client:

            # Varre cada um dos tempos configurados
            for inter in intervalos:
                # Soma o tempo necessário para descobrir o alvo do disparo
                alvo = agora + timedelta(minutes=inter["minutos"])

                data_alvo = alvo.strftime("%Y-%m-%d")  # Ex: 2026-05-23
                horario_alvo = alvo.strftime("%H:%M")  # Ex: 14:30
                campo_controle = inter["campo_banco"]

                print(
                    f"⏰ [CRON] Buscando agendamentos ativos para daqui a {inter['label']}: {data_alvo} às {horario_alvo}..."
                )

                # 3. Busca no banco de dados filtrando pela data e hora exata daquele alvo
                agendamentos_ref = (
                    db.collection("agendamentos")
                    .where(filter=FieldFilter("data", "==", data_alvo))
                    .where(filter=FieldFilter("horario", "==", horario_alvo))
                    .where(filter=FieldFilter("status", "==", "agendado"))
                    .stream()
                )

                for doc in agendamentos_ref:
                    total_encontrados += 1
                    ag = doc.to_dict()
                    ag_id = doc.id

                    # 🔥 CHECAGEM PRO: Se já mandou ESSE lembrete específico, pula pro próximo
                    if ag.get(campo_controle):
                        print(
                            f"ℹ️ Agendamento {ag_id} já teve o lembrete de {inter['label']} enviado. Pulando."
                        )
                        continue

                    # Busca o telefone do cliente usando o idCliente do agendamento
                    id_cliente = ag.get("idCliente")
                    user_doc = db.collection("usuarios").document(id_cliente).get()

                    if not user_doc.exists:
                        print(
                            f"⚠️ Usuário com ID {id_cliente} não existe no banco. Não há como enviar."
                        )
                        continue

                    user_data = user_doc.to_dict()
                    telefone = user_data.get("telefone")
                    nome = user_data.get("nome", "Cliente").split(" ")[0]

                    if not telefone:
                        print(
                            f"⚠️ O usuário {nome} (ID: {id_cliente}) não possui telefone cadastrado."
                        )
                        continue

                    nome_servico = ag.get("nomeServico", "serviço agendado")
                    nome_profissional = ag.get("nomeBarbeiro", "nosso profissional")
                    
                    # 4. Prepara o disparo para o Vortix Endpoints
                    vortix_url = "https://vortix-endpoints-298894543925.us-central1.run.app/api/v1/dispatch"
                    headers = {
                        "Authorization": "Bearer vtx_live_ed8d2590381245d6a0e9ea69b306e2b585db6b",
                        "Content-Type": "application/json",
                    }

                    # Payload estruturado para o template da Meta
                    payload = {
                        "phone": telefone,
                        "type": "template",
                        "content": {
                            "name": "alerta_de_agendamento_proximo",
                            "language": {"code": "pt_BR"},
                            "components": [
                                {
                                    "type": "body",
                                    "parameters": [
                                        {"type": "text", "text": nome},
                                        {"type": "text", "text": inter["label"]},
                                        {"type": "text", "text": nome_servico},
                                        {"type": "text", "text": nome_profissional},
                                    ],
                                }
                            ],
                        },
                    }

                    print(
                        f"🚀 Disparando lembrete de {inter['label']} via Vortix para {nome} ({telefone})..."
                    )

                    # 5. Executa a requisição HTTP POST para o disparo
                    res = await client.post(vortix_url, headers=headers, json=payload)

                    if res.status_code in [200, 201]:
                        # 6. Salva especificamente qual lembrete foi enviado para nunca repetir
                        db.collection("agendamentos").document(ag_id).update(
                            {campo_controle: True}
                        )
                        print(
                            f"✅ Lembrete de {inter['label']} enviado com sucesso para {nome}! Banco atualizado."
                        )
                        enviados += 1
                    else:
                        print(
                            f"❌ ERRO AO ENVIAR WHATSAPP para {nome} ({telefone}): Status {res.status_code}"
                        )
                        print(f"🔍 Detalhes do Erro retornados pela API: {res.text}")

        print(
            f"🏁 [CRON] Varredura completa finalizada. Total Encontrados: {total_encontrados} | Total Enviados: {enviados}"
        )
        return {
            "status": "success",
            "message": f"Verificação de múltiplos intervalos concluída. Encontrados: {total_encontrados} | Enviados: {enviados}",
        }

    except Exception as e:
        print(f"💥 ERRO CRÍTICO NO ENDPOINT DO CRON: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Erro interno no servidor: {str(e)}"
        )
