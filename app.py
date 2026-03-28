import streamlit as st
import fitz  # PyMuPDF
import re
import io
import zipfile
import itertools
from collections import defaultdict
import logging
from PyPDF2 import PdfReader, PdfWriter

# Suprimir avisos do pdfminer
logging.getLogger('pdfminer.pdfpage').setLevel(logging.ERROR)

# Inicializa o armazenamento de sessão para manter o ZIP gerado na tela após a limpeza
if "processed_zip" not in st.session_state:
    st.session_state.processed_zip = None

# ==========================================
# CLASSE DE LOG EM TEMPO REAL
# ==========================================
class StreamlitLogger:
    def __init__(self):
        self.log_text = ""
        self.log_placeholder = st.empty()

    def print(self, message):
        """Adiciona a mensagem ao log e atualiza a interface."""
        self.log_text += str(message) + "\n"
        self.log_placeholder.code(self.log_text, language="bash")

# ==========================================
# FUNÇÕES DE EXTRAÇÃO BASE
# ==========================================
def extrair_dados_basicos(texto):
    """Extrai rapidamente CPF e Valor para guiar o agrupamento de páginas de Holerites."""
    cpf = None
    valor = None
    match_cpf = re.search(r'CPF:\s*([\d\.\-]+)', texto, re.IGNORECASE)
    if match_cpf:
        cpf_bruto = match_cpf.group(1).strip()
        if len(cpf_bruto) == 11 and cpf_bruto.isdigit():
            cpf = f"{cpf_bruto[:3]}.{cpf_bruto[3:6]}.{cpf_bruto[6:9]}-{cpf_bruto[9:]}"
        else:
            cpf = cpf_bruto 

    match_valor = re.search(r'SALÁRIO LÍQUIDO:[^\d]*([\d\.]+,[\d]{2})', texto, re.IGNORECASE)
    if match_valor:
        valor = match_valor.group(1)
    return cpf, valor

def extrair_titulo_holerite(texto):
    """Extrai o título completo baseando-se nas 'linhas âncora' identificadas."""
    if not texto: return "Titulo_Vazio"

    cpf, valor = extrair_dados_basicos(texto)
    if not cpf: cpf = "CPFNaoEncontrado"
    if not valor: valor = "ValorNaoEncontrado"

    nome = "NomeNaoEncontrado"
    periodo = "MesAnoNaoEncontrado"

    # Quebra o texto em linhas e remove espaços vazios
    linhas = [linha.strip() for linha in texto.split('\n') if linha.strip()]
    
    for i, linha in enumerate(linhas):
        linha_upper = linha.upper()

        # 1. Extração do Período/Competência (Regra exata definida pelo usuário)
        # Exatamente 7 caracteres, começa com 0 ou 1, tem um espaço no meio e ano entre 2000-2040.
        if periodo == "MesAnoNaoEncontrado" and len(linha) == 7:
            if re.match(r'^[01]\d\s(?:20[0-3]\d|2040)$', linha):
                periodo = linha.replace(' ', '_')

        # 2. Extração do Nome (Lógica nova: Linha imediatamente ABAIXO do CPF)
        if 'CPF:' in linha_upper and nome == "NomeNaoEncontrado":
            if i + 1 < len(linhas): # Garante que existe uma linha de baixo
                candidato = linhas[i+1].strip()
                # Verifica se não bateu na palavra DATA por algum motivo de formatação
                if not candidato.upper().startswith('DATA'):
                    nome = re.sub(r'\d+', '', candidato).strip() # Limpa possíveis números

    # Limpeza final de formatação do nome
    if nome != "NomeNaoEncontrado": 
        nome = re.sub(r'\s+', ' ', nome).strip() 
        nome = nome.strip(' :,-_')

    titulo = f"{nome} - {cpf} - {periodo} - R$ {valor}"
    titulo_sanitizado = re.sub(r'[\\/*?:"<>|]', '_', titulo)
    return re.sub(r'\s+', ' ', titulo_sanitizado).strip()

def extrair_dados_comprovante(texto_pagina):
    """Extrai os dados do comprovante usando expressões regulares."""
    cpf_pattern = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
    valor_pattern = re.compile(r"\b\d{1,3}(?:\.\d{3})*,\d{2}\b")
    nome_pattern = re.compile(r"(?:Funcionário|Favorecido):\s*(.*?)\s*CPF:", re.DOTALL)

    cpf_match = cpf_pattern.search(texto_pagina)
    valor_match = valor_pattern.search(texto_pagina)
    nome_match = nome_pattern.search(texto_pagina)

    if cpf_match and valor_match:
        cpf = cpf_match.group(0)
        valor = valor_match.group(0)
        nome = nome_match.group(1).strip() if nome_match else ""
        return f"{nome} - {cpf} - R$ {valor} - RECIBO" if nome else f"{cpf} - R$ {valor} - RECIBO"
    return None

# ==========================================
# UTILITÁRIO DE UPLOAD
# ==========================================
def extrair_pdfs_de_uploads(uploaded_files, logger):
    """Lê ficheiros PDF soltos ou descompacta ficheiros ZIP diretamente na memória."""
    arquivos_extraidos = []
    for file in uploaded_files:
        if file.name.lower().endswith('.zip'):
            logger.print(f"📦 Extraindo ZIP: '{file.name}'")
            try:
                with zipfile.ZipFile(file, 'r') as z:
                    for zip_info in z.infolist():
                        if zip_info.filename.lower().endswith('.pdf') and not zip_info.filename.startswith('__MACOSX'):
                            arquivos_extraidos.append((zip_info.filename.split('/')[-1], z.read(zip_info.filename)))
            except zipfile.BadZipFile:
                logger.print(f"❌ Erro: O ficheiro '{file.name}' não é um ZIP válido.")
        elif file.name.lower().endswith('.pdf'):
            arquivos_extraidos.append((file.name, file.read()))
    return arquivos_extraidos

# ==========================================
# PROCESSAMENTO ESPECÍFICO
# ==========================================
def processar_holerites(arquivos, logger, doc_nao_classificadas):
    """Processa e agrupa páginas de holerites baseando-se no CPF e Valor."""
    holerites_dict = {}
    agrupamento = {}

    for nome, pdf_bytes in arquivos:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        cpf_atual = None
        valor_atual = None

        for i in range(len(doc)):
            texto = doc[i].get_text("text")
            cpf, valor = extrair_dados_basicos(texto)

            if cpf: cpf_atual = cpf
            if valor: valor_atual = valor

            if not cpf_atual:
                doc_nao_classificadas.insert_pdf(doc, from_page=i, to_page=i)
                logger.print(f"  ⚪ Pág {i+1} de '{nome}' sem CPF -> Não Classificada.")
                continue

            chave = (cpf_atual, valor_atual)
            if chave not in agrupamento:
                agrupamento[chave] = fitz.open()
            agrupamento[chave].insert_pdf(doc, from_page=i, to_page=i)
        doc.close()

    for chave, pdf_doc in agrupamento.items():
        texto_completo = ""
        for i in range(len(pdf_doc)):
            texto_completo += pdf_doc[i].get_text("text") + "\n"

        titulo = extrair_titulo_holerite(texto_completo)
        nome_arquivo = f"{titulo}.pdf"
        
        contador = 1
        nome_base = titulo
        while nome_arquivo in holerites_dict:
            nome_arquivo = f"{nome_base}_{contador}.pdf"
            contador += 1

        holerites_dict[nome_arquivo] = pdf_doc.write()
        logger.print(f"  🟢 HOLERITE -> '{nome_arquivo}' (Agrupou {len(pdf_doc)} páginas)")
        pdf_doc.close()

    return holerites_dict

def processar_comprovantes(arquivos, logger, doc_nao_classificadas):
    """Processa páginas de comprovantes isoladamente."""
    comprovantes_dict = {}
    for nome, pdf_bytes in arquivos:
        doc_fitz = fitz.open(stream=pdf_bytes, filetype="pdf")
        for i in range(len(doc_fitz)):
            texto_fitz = doc_fitz[i].get_text("text")
            titulo = extrair_dados_comprovante(texto_fitz)

            if titulo:
                nome_arquivo = f"{titulo}.pdf"
                contador = 1
                while nome_arquivo in comprovantes_dict:
                    nome_arquivo = f"{titulo}_{contador}.pdf"
                    contador += 1
                    
                nova_pagina = fitz.open()
                nova_pagina.insert_pdf(doc_fitz, from_page=i, to_page=i)
                comprovantes_dict[nome_arquivo] = nova_pagina.write()
                nova_pagina.close()
                logger.print(f"  🔵 COMPROVANTE -> '{nome_arquivo}'")
            else:
                logger.print(f"  ⚠ Pág {i+1} de '{nome}' falhou na extração. Enviando p/ NAO CLASSIFICADAS.")
                doc_nao_classificadas.insert_pdf(doc_fitz, from_page=i, to_page=i)
        doc_fitz.close()
    return comprovantes_dict

# ==========================================
# UNIÃO DOS ARQUIVOS
# ==========================================
def extrair_cpf_e_valor(nome_arquivo):
    """Extrai CPF e valor do nome do ficheiro para a lógica de união."""
    match_cpf = re.search(r'(\d{3}\.\d{3}\.\d{3}-\d{2})', nome_arquivo)
    match_valor = re.search(r'R\$\s*([\d\.,]+,\d{2})', nome_arquivo)
    cpf_str = match_cpf.group(1) if match_cpf else None
    valor_float = None
    if match_valor:
        try:
            valor_float = float(match_valor.group(1).replace('.', '').replace(',', '.'))
        except: pass
    return cpf_str, valor_float

def unir_arquivos_memoria(holerites_dict, comprovantes_dict, logger):
    """Une holerites e comprovantes baseando-se em combinações de valores por CPF."""
    arquivos_finais = {}
    grupos_por_cpf = defaultdict(lambda: {'originais': [], 'recibos': []})

    for nome, pdf_bytes in holerites_dict.items():
        cpf, valor = extrair_cpf_e_valor(nome)
        if cpf: grupos_por_cpf[cpf]['originais'].append({'nome': nome, 'valor': valor, 'bytes': pdf_bytes})

    for nome, pdf_bytes in comprovantes_dict.items():
        cpf, valor = extrair_cpf_e_valor(nome)
        if cpf: grupos_por_cpf[cpf]['recibos'].append({'nome': nome, 'valor': valor, 'bytes': pdf_bytes, 'usado': False})

    tolerancia = 0.01

    for cpf, dados in grupos_por_cpf.items():
        originais = dados['originais']
        recibos = dados['recibos']

        for original in originais:
            valor_original = original['valor']
            uniao_realizada = False

            if valor_original is None:
                arquivos_finais[original['nome']] = original['bytes']
                continue

            # Lógica 1: Correspondência exata de valor
            for recibo in recibos:
                if not recibo['usado'] and recibo['valor'] is not None and abs(recibo['valor'] - valor_original) < tolerancia:
                    novo_nome = original['nome'].replace(".pdf", " - RECIBO_COMPROVANTE.pdf")
                    writer = PdfWriter()
                    pdf_orig = PdfReader(io.BytesIO(original['bytes']))
                    for p in pdf_orig.pages: writer.add_page(p)
                    
                    writer.add_page(PdfReader(io.BytesIO(recibo['bytes'])).pages[0])
                    
                    out_stream = io.BytesIO()
                    writer.write(out_stream)
                    arquivos_finais[novo_nome] = out_stream.getvalue()
                    
                    recibo['usado'] = True
                    uniao_realizada = True
                    logger.print(f" [SUCESSO] Unido match exato: {novo_nome}")
                    break

            # Lógica 2: Combinação de múltiplos recibos para o mesmo holerite
            if not uniao_realizada:
                recibos_disponiveis = [r for r in recibos if not r['usado']]
                melhor_combinacao = None

                for r_count in range(1, len(recibos_disponiveis) + 1):
                    for combinacao in itertools.combinations(recibos_disponiveis, r_count):
                        soma = sum(r['valor'] for r in combinacao if r['valor'] is not None)
                        if abs(soma - valor_original) < tolerancia:
                            melhor_combinacao = combinacao
                            break
                    if melhor_combinacao: break
                    
                if melhor_combinacao:
                    novo_nome = original['nome'].replace(".pdf", " - RECIBO_COMPROVANTE.pdf")
                    writer = PdfWriter()
                    pdf_orig = PdfReader(io.BytesIO(original['bytes']))
                    for p in pdf_orig.pages: writer.add_page(p)
                    
                    recibos_ordenados = sorted(melhor_combinacao, key=lambda x: x['valor'] or 0)
                    for rec in recibos_ordenados:
                        writer.add_page(PdfReader(io.BytesIO(rec['bytes'])).pages[0])
                        rec['usado'] = True
                        
                    out_stream = io.BytesIO()
                    writer.write(out_stream)
                    arquivos_finais[novo_nome] = out_stream.getvalue()
                    
                    uniao_realizada = True
                    logger.print(f" [SUCESSO] Unido combinações: {novo_nome}")

            if not uniao_realizada:
                logger.print(f" [Aviso] Sem combinações válidas p/ {original['nome']}. Mantendo isolado.")
                arquivos_finais[original['nome']] = original['bytes']

    return arquivos_finais

# ==========================================
# INTERFACE STREAMLIT
# ==========================================
st.set_page_config(page_title="Processador de Holerites e Comprovantes", layout="wide")
st.title("📄 Processador e Unificador de PDFs")

# O FORMULÁRIO: Garante que os arquivos enviados sejam limpos após o clique
with st.form("upload_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 📄 1. Enviar Holerites")
        st.markdown("Arraste PDFs soltos ou um único `.zip`")
        up_holerites = st.file_uploader("", type=["pdf", "zip"], accept_multiple_files=True, key="holerites")

    with col2:
        st.markdown("### 🧾 2. Enviar Comprovantes")
        st.markdown("Arraste PDFs soltos ou um único `.zip`")
        up_comprovantes = st.file_uploader("", type=["pdf", "zip"], accept_multiple_files=True, key="comprovantes")

    st.markdown("---")
    submit_button = st.form_submit_button("🚀 Iniciar Processamento", use_container_width=True)

# Container onde a mensagem e o botão de download irão morar (Acima do log)
container_resultados = st.container()

if submit_button:
    
    # Limpa o estado anterior
    st.session_state.processed_zip = None

    qtd_holerites = len(up_holerites) if up_holerites else 0
    qtd_comprovantes = len(up_comprovantes) if up_comprovantes else 0

    if qtd_holerites > 50 or qtd_comprovantes > 50:
        st.error("🛑 **Limite de ficheiros excedido!**\n\n"
                 "Selecionou mais de 50 ficheiros num dos campos. O limite para envio de ficheiros soltos é de **50 PDFs de cada vez**.\n\n"
                 "👉 **O que fazer:** Coloque todos os seus PDFs dentro de uma pasta compactada (**ficheiro .zip**) e faça o upload de apenas **um único ficheiro .zip** na área correspondente!")
    
    elif not up_holerites and not up_comprovantes:
        st.warning("⚠️ Por favor, faça o upload de ficheiros em pelo menos uma das áreas acima para iniciar o processo.")
    
    else:
        st.markdown("### 🖥️ Terminal de Processamento")
        app_logger = StreamlitLogger()
        doc_nao_classificadas = fitz.open()
        
        arq_holerites = extrair_pdfs_de_uploads(up_holerites, app_logger) if up_holerites else []
        arq_comprovantes = extrair_pdfs_de_uploads(up_comprovantes, app_logger) if up_comprovantes else []

        app_logger.print("\n>>> PROCESSANDO HOLERITES...")
        holerites_sep = processar_holerites(arq_holerites, app_logger, doc_nao_classificadas) if arq_holerites else {}
        
        app_logger.print("\n>>> PROCESSANDO COMPROVANTES...")
        comprovantes_sep = processar_comprovantes(arq_comprovantes, app_logger, doc_nao_classificadas) if arq_comprovantes else {}
            
        app_logger.print("\n>>> UNINDO HOLERITES E COMPROVANTES...")
        pdfs_finais = unir_arquivos_memoria(holerites_sep, comprovantes_sep, app_logger)
        
        if len(doc_nao_classificadas) > 0:
            app_logger.print(f"\n>>> FORAM ENCONTRADAS {len(doc_nao_classificadas)} PÁGINA(S) NÃO CLASSIFICADA(S)!")
            pdfs_finais["NAO_CLASSIFICADAS.pdf"] = doc_nao_classificadas.write()
        doc_nao_classificadas.close()
        
        app_logger.print("\n>>> FINALIZADO! Preparando ficheiro ZIP de saída...")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for nome_arquivo, pdf_bytes in pdfs_finais.items():
                zip_file.writestr(nome_arquivo, pdf_bytes)
            zip_file.writestr("relatorio_processamento.txt", app_logger.log_text)

        # Salva o arquivo em memória da sessão
        st.session_state.processed_zip = zip_buffer.getvalue()

# Exibe a mensagem de sucesso e o botão APENAS se o arquivo estiver salvo na sessão
if st.session_state.processed_zip:
    with container_resultados:
        st.success("✨ Processamento concluído com sucesso! \n\n*Os campos de upload acima foram limpos e estão prontos para uma nova execução.*")
        st.download_button(
            label="⬇️ Baixar Arquivos Processados (.zip)",
            data=st.session_state.processed_zip,
            file_name="arquivos_processados.zip",
            mime="application/zip",
            use_container_width=True
        )
