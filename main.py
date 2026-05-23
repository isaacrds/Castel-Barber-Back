import os
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
    "universe_domain": "googleapis.com"
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
class VerifyUpdate(BaseModel):
    phone: str
    status: str  # Pode ser: 'verify', 'negative', ou 'await'

# ==========================================
# 4. ROTAS E ENDPOINTS
# ==========================================
@app.post("/webhook/verify")
async def update_user_verification(payload: VerifyUpdate):
    """
    Recebe o webhook do Zapia e atualiza o Firestore da Castel Barber em Real-Time.
    """
    try:
        telefone_limpo = ''.join(filter(str.isdigit, payload.phone))
        
        users_ref = db.collection("usuarios").where(filter=FieldFilter("telefone", "==", telefone_limpo)).stream()
        
        user_doc = None
        for doc in users_ref:
            user_doc = doc
            break 
            
        if not user_doc:
            raise HTTPException(status_code=404, detail="Usuário não encontrado com este telefone no banco.")

        db.collection("usuarios").document(user_doc.id).update({
            "is_verify": payload.status
        })

        return {
            "status": "success", 
            "message": f"Status de verificação alterado para '{payload.status}' com sucesso."
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))