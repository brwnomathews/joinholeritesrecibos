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

# ==========================================
# GERENCIAMENTO DE ESTADO (MEMÓRIA DA TELA)
# ==========================================
if "reset_key" not in st.session_state: st.session_state.reset_key = 0
if "processed_zip" not in st.session_state: st.session_state.processed_zip = None
if "terminal_log" not in st.session_state: st.session_state.terminal_log = ""
if "success_msg" not in st.session_state: st.session_state.success_msg = False
if "error_msg" not in st.session_state: st.session_state.error_msg = ""

# ==========================================
# CLASSE DE LOG EM TEMPO REAL
# ==========================================
class StreamlitLogger:
    def __init__(self, placeholder):
        self.log_text = ""
        self.placeholder = placeholder

    def print(self, message):
        """Adiciona a mensagem ao log e atualiza a interface."""
        self.log_text += str(message) + "\n"
        self.placeholder.code(self.log_text, language="bash")

# ==========================================
# FUNÇÃO UNIFICADA DE EXTRAÇÃO (HOLERITES)
# ==========================================
def extrair_dados_completos(texto):
    """Extrai CPF, Valor e Nome. A competência agora é injetada manualmente."""
    cpf = None
    valor = None
    nome = "NomeNaoEncontrado"

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

    linhas = [linha.strip() for linha in texto.split('\n') if linha.strip()]
    for i, linha in enumerate(linhas):
        if 'CPF:' in linha.upper() and nome == "NomeNaoEncontrado":
            if i + 1 < len(linhas):
                candidato = linhas[i+1].strip()
                if not candidato.upper().startswith('DATA'):
                    nome = re.sub(r'\d+', '', candidato).strip()

    if nome != "NomeNaoEncontrado": 
        nome = re.sub(r'\s+', ' ', nome).strip() 
        nome = nome.strip(' :,-_')

    return cpf, valor, nome

def extrair_dados_comprovante(texto_pagina):
    """Extrai APENAS CPF e Valor."""
    cpf_pattern = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
    valor_pattern = re.compile(r"\b\d{1,3}(?:\.\d{3})*,\d{2}\b")

    cpf_match = cpf_pattern.search(texto_pagina)
    valor_match = valor_pattern.search(texto_pagina)

    cpf = cpf_match.group(0) if cpf_match else None
    valor = valor_match.group(0) if valor_match else None

    return cpf, valor

# ==========================================
# UTILITÁRIO DE UPLOAD BLINDADO
# ==========================================
def extrair_pdfs_de_uploads(uploaded_files, logger):
    """Lê ficheiros PDF soltos ou descompacta ficheiros ZIP ignorando ficheiros vazios e ocultos."""
    arquivos_extraidos = []
    for file in uploaded_files:
        if file.name.lower().endswith('.zip'):
            logger.print(f"📦 Extraindo ZIP: '{file.name}'")
            try:
                with zipfile.ZipFile(file, 'r') as z:
                    for zip_info in z.infolist():
                        nome_arquivo = zip_info.filename.split('/')[-1]
                        if zip_info.filename.lower().endswith('.pdf') and '__MACOSX' not in zip_info.filename and not nome_arquivo.startswith('._'):
                            pdf_bytes = z.read(zip_info.filename)
                            if len(pdf_bytes) > 0:
                                arquivos_extraidos.append((nome_arquivo, pdf_bytes))
            except zipfile.BadZipFile:
                logger.print(f"❌ Erro: O ficheiro '{file.name}' não é um ZIP válido.")
        elif file.name.lower().endswith('.pdf'):
            pdf_bytes = file.read()
            if len(pdf_bytes) > 0:
                arquivos_extraidos.append((file.name, pdf_bytes))
    return arquivos_extraidos

# ==========================================
# PROCESSAMENTO ESPECÍFICO
# ==========================================
def processar_holerites(arquivos, logger, doc_nao_classificadas, periodo_global):
    holerites_dict = {}
    agrupamento = {}
    memoria_cpf = defaultdict(lambda: {'nome': "NomeNaoEncontrado"})

    for nome_arq, pdf_bytes in arquivos:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        cpf_atual = None
        valor_atual = None

        for i in range(len(doc)):
            texto = doc[i].get_text("text")
            cpf, valor, nome = extrair_dados_completos(texto)

            if cpf: cpf_atual = cpf
            if valor: valor_atual = valor
            
            if cpf_atual and nome != "NomeNaoEncontrado":
                memoria_cpf[cpf_atual]['nome'] = nome

            if not cpf_atual:
                doc_nao_classificadas.insert_pdf(doc, from_page=i, to_page=i)
                logger.print(f"  ⚪ Pág {i+1} de '{nome_arq}' sem CPF -> Não Classificada.")
                continue

            chave = (cpf_atual, valor_atual)
            if chave not in agrupamento:
                agrupamento[chave] = fitz.open()
            agrupamento[chave].insert_pdf(doc, from_page=i, to_page=i)
        doc.close()

    for chave, pdf_doc in agrupamento.items():
        cpf_grupo, valor_grupo = chave
        texto_completo = ""
        for i in range(len(pdf_doc)):
            texto_completo += pdf_doc[i].get_text("text") + "\n"

        cpf, valor, nome = extrair_dados_completos(texto_completo)

        if nome == "NomeNaoEncontrado": nome = memoria_cpf[cpf_grupo]['nome']
        if not cpf: cpf = cpf_grupo
        if not valor: valor = valor_grupo

        titulo = f"{nome} - {cpf} - {periodo_global} - R$ {valor}"
        titulo_sanitizado = re.sub(r'[\\/*?:"<>|]', '_', titulo)
        titulo_limpo = re.sub(r'\s+', ' ', titulo_sanitizado).strip()
        nome_arquivo = f"{titulo_limpo}.pdf"
        
        contador = 1
        nome_base = titulo_limpo
        while nome_arquivo in holerites_dict:
            nome_arquivo = f"{nome_base}_{contador}.pdf"
            contador += 1

        holerites_dict[nome_arquivo] = pdf_doc.write()
        logger.print(f"  🟢 HOLERITE -> '{nome_arquivo}' (Agrupou {len(pdf_doc)} páginas)")
        pdf_doc.close()

    return holerites_dict

def processar_comprovantes(arquivos, logger, doc_nao_classificadas, map_cpf_nome, map_nome_cpf):
    comprovantes_dict = {}
    
    for nome_arq, pdf_bytes in arquivos:
        doc_fitz = fitz.open(stream=pdf_bytes, filetype="pdf")
        for i in range(len(doc_fitz)):
            texto_fitz = doc_fitz[i].get_text("text")
            
            cpf, valor = extrair_dados_comprovante(texto_fitz)
            nome_final = None

            if not cpf and valor:
                texto_upper = re.sub(r'\s+', ' ', texto_fitz.upper())
                for nome_conhecido, cpf_associado in map_nome_cpf.items():
                    if nome_conhecido in texto_upper:
                        cpf = cpf_associado
                        nome_final = map_cpf_nome[cpf] 
                        logger.print(f"  🔍 Resgate! CPF {cpf} encontrado via Nome '{nome_conhecido}'")
                        break

            if cpf and valor:
                if not nome_final and cpf in map_cpf_nome:
                    nome_final = map_cpf_nome[cpf]
                
                if nome_final:
                    titulo = f"{nome_final} - {cpf} - R$ {valor} - RECIBO"
                else:
                    titulo = f"{cpf} - R$ {valor} - RECIBO"
                
                titulo_sanitizado = re.sub(r'[\\/*?:"<>|]', '_', titulo)
                nome_arquivo = f"{titulo_sanitizado}.pdf"

                contador = 1
                nome_base = titulo_sanitizado
                while nome_arquivo in comprovantes_dict:
                    nome_arquivo = f"{nome_base}_{contador}.pdf"
                    contador += 1
                    
                nova_pagina = fitz.open()
                nova_pagina.insert_pdf(doc_fitz, from_page=i, to_page=i)
                comprovantes_dict[nome_arquivo] = nova_pagina.write()
                nova_pagina.close()
                logger.print(f"  🔵 COMPROVANTE -> '{nome_arquivo}'")
            else:
                logger.print(f"  ⚠ Pág {i+1} de '{nome_arq}' falhou na extração. Enviando p/ NAO CLASSIFICADAS.")
                doc_nao_classificadas.insert_pdf(doc_fitz, from_page=i, to_page=i)
        doc_fitz.close()
    return comprovantes_dict

# ==========================================
# UNIÃO DOS ARQUIVOS
# ==========================================
def extrair_cpf_e_valor(nome_arquivo):
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

            for recibo in recibos:
                if not recibo['usado'] and recibo['valor'] is not None and abs(recibo['valor'] - valor_original) < tolerancia:
                    novo_nome = "_UNIDO - " + original['nome'].replace(".pdf", " - RECIBO_COMPROVANTE.pdf")
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
                    novo_nome = "_UNIDO - " + original['nome'].replace(".pdf", " - RECIBO_COMPROVANTE.pdf")
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

# Exibe a mensagem de erro da trava caso exista e a limpa em seguida para não ficar travada na tela
if st.session_state.error_msg:
    st.error(st.session_state.error_msg)
    st.session_state.error_msg = ""

# ----------------------------------------------------
# LAYOUT DE BLOCO ÚNICO SUPERIOR
# ----------------------------------------------------
col_comp, col_hol, col_compr = st.columns([1, 1.5, 1.5])

with col_comp:
    st.markdown("### 📅 1. Competência")
    meses_opcoes = [f"{i:02d}" for i in range(1, 14)] # 01 até 13
    mes_selecionado = st.selectbox("Mês", options=meses_opcoes, index=None, placeholder="Selecione o Mês...")
    
    anos_opcoes = [str(i) for i in range(2024, 2031)] # 2024 até 2030
    ano_selecionado = st.selectbox("Ano", options=anos_opcoes, index=None, placeholder="Selecione o Ano...")

with col_hol:
    st.markdown("### 📄 2. Holerites")
    up_holerites = st.file_uploader("Arraste PDFs soltos ou um único .zip", type=["pdf", "zip"], accept_multiple_files=True, key=f"hol_{st.session_state.reset_key}")

with col_compr:
    st.markdown("### 🧾 3. Comprovantes")
    up_comprovantes = st.file_uploader("Arraste PDFs soltos ou um único .zip", type=["pdf", "zip"], accept_multiple_files=True, key=f"comp_{st.session_state.reset_key}")

st.markdown("---")

# ----------------------------------------------------
# TRAVA DE LIMITE DE ARQUIVOS (MAIS DE 50) - Feedback Imediato
# ----------------------------------------------------
if (up_holerites and len(up_holerites) > 50) or (up_comprovantes and len(up_comprovantes) > 50):
    st.session_state.error_msg = (
        "🛑 **Limite de ficheiros excedido!**\n\n"
        "Selecionou mais de 50 ficheiros num dos campos. O limite para envio de ficheiros soltos é de **50 PDFs de cada vez**.\n\n"
        "👉 **O que fazer:** Coloque todos os seus PDFs dentro de uma pasta compactada (**ficheiro .zip**) e faça o upload de apenas **um único ficheiro .zip** na área correspondente!"
    )
    st.session_state.reset_key += 1 # Esvazia as caixas de upload
    st.rerun() # Reinicia a tela instantaneamente para aplicar a limpeza e mostrar o erro

# ----------------------------------------------------
# LAYOUT DE BOTÕES (50% Cada, lado a lado)
# ----------------------------------------------------
col_btn_start, col_btn_down = st.columns(2)

with col_btn_start:
    submit_button = st.button("🚀 Iniciar Processamento", use_container_width=True)

with col_btn_down:
    if st.session_state.processed_zip:
        st.download_button(
            label="⬇️ Baixar Arquivos Processados (.zip)",
            data=st.session_state.processed_zip,
            file_name="arquivos_processados.zip",
            mime="application/zip",
            use_container_width=True
        )
    else:
        st.button("⬇️ Baixar Arquivos Processados (.zip)", disabled=True, use_container_width=True)

# ----------------------------------------------------
# RENDERIZAÇÃO DE RESULTADOS NA TELA
# ----------------------------------------------------
if st.session_state.success_msg:
    st.success("✨ Processamento concluído com sucesso! \n\n*Os campos de upload acima foram limpos e estão prontos para uma nova execução.*")
    st.markdown("### 🖥️ Terminal de Processamento")
    st.code(st.session_state.terminal_log, language="bash")

# ----------------------------------------------------
# LÓGICA DE EXECUÇÃO
# ----------------------------------------------------
if submit_button:
    
    st.session_state.success_msg = False
    st.session_state.terminal_log = ""
    st.session_state.processed_zip = None

    if not mes_selecionado or not ano_selecionado:
        st.error("🛑 **Atenção:** É obrigatório selecionar o **Mês** e o **Ano** de Competência na seção 1 para prosseguir!")
    
    elif not up_holerites and not up_comprovantes:
        st.warning("⚠️ Por favor, faça o upload de ficheiros em pelo menos uma das áreas acima para iniciar o processo.")
    
    else:
        st.markdown("### 🖥️ Terminal de Processamento")
        terminal_placeholder = st.empty()
        app_logger = StreamlitLogger(terminal_placeholder)
        
        doc_nao_classificadas = fitz.open()
        
        arq_holerites = extrair_pdfs_de_uploads(up_holerites, app_logger) if up_holerites else []
        arq_comprovantes = extrair_pdfs_de_uploads(up_comprovantes, app_logger) if up_comprovantes else []

        periodo_global = f"{mes_selecionado}_{ano_selecionado}"

        app_logger.print("\n>>> PROCESSANDO HOLERITES...")
        
        holerites_sep = processar_holerites(arq_holerites, app_logger, doc_nao_classificadas, periodo_global) if arq_holerites else {}
        
        map_cpf_nome = {}
        map_nome_cpf = {}
        for nome_arquivo in holerites_sep.keys():
            partes = nome_arquivo.replace(".pdf", "").split(" - ")
            if len(partes) >= 2:
                nome = partes[0].strip()
                cpf = partes[1].strip()
                if nome.upper() != "NOMENAOENCONTRADO" and re.match(r'\d{3}\.\d{3}\.\d{3}-\d{2}', cpf):
                    map_cpf_nome[cpf] = nome
                    map_nome_cpf[nome.upper()] = cpf
        
        app_logger.print("\n>>> PROCESSANDO COMPROVANTES...")
        
        comprovantes_sep = processar_comprovantes(arq_comprovantes, app_logger, doc_nao_classificadas, map_cpf_nome, map_nome_cpf) if arq_comprovantes else {}
            
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

        st.session_state.terminal_log = app_logger.log_text
        st.session_state.processed_zip = zip_buffer.getvalue()
        st.session_state.success_msg = True
        st.session_state.reset_key += 1 
        
        st.rerun()
