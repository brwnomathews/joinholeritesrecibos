import streamlit as st
import fitz  # PyMuPDF
import pdfplumber
import re
import io
import zipfile
import itertools
from collections import defaultdict
import logging
from PyPDF2 import PdfReader, PdfWriter

# Suprimir warnings do pdfminer (usado pelo pdfplumber)
logging.getLogger('pdfminer.pdfpage').setLevel(logging.ERROR)

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
# FUNÇÃO DE CLASSIFICAÇÃO DE TEXTO
# ==========================================
def classificar_texto_pagina(texto_pagina):
    """Classifica a página baseada no texto extraído."""
    if not texto_pagina:
        return "DESCONHECIDO"
        
    if "RECIBO" in texto_pagina and "PROVENTOS" in texto_pagina:
        return "HOLERITE"
    
    condicao_comprovante_1 = "Comprovante" in texto_pagina and "Pagamento" in texto_pagina and "Bradesco" in texto_pagina
    condicao_comprovante_2 = "favorecido" in texto_pagina and "Bradesco" in texto_pagina
    
    if condicao_comprovante_1 or condicao_comprovante_2:
        return "COMPROVANTE"
        
    return "DESCONHECIDO"

# ==========================================
# FUNÇÕES DE EXTRAÇÃO
# ==========================================
def extrair_titulo_holerite(texto):
    if not texto:
        return "Titulo_Pagina_Vazia_ou_Erro"

    linhas = texto.split('\n')
    texto1 = re.sub(r'\d+', '', linhas[4]).strip() if len(linhas) > 4 and linhas[4] else "InfoNaoEncontrada1"
    
    texto5 = "InfoNaoEncontrada5"
    if len(linhas) > 2 and linhas[2]:
        if "CPF:" in linhas[2]:
            try:
                texto5 = linhas[2].split("CPF:")[1].strip()
            except IndexError:
                texto5 = ""
        else:
            texto5 = "CPF_NaoEncontrado"

    texto2 = "InfoNaoEncontrada2"
    if len(linhas) > 5 and linhas[5]:
        texto2 = linhas[5].split("Data")[0].strip() if "Data" in linhas[5] else linhas[5].strip()

    texto3 = "LiquidoNaoEncontrado"
    contador = 0
    for linha in linhas:
        if linha and "Líquido a Pagar" in linha:
            contador += 1
            if contador == 2:
                try:
                    texto3 = linha.split("Líquido a Pagar", 1)[1].strip()
                except Exception:
                    texto3 = "ValorNaoEncontrado"
                break

    texto4_lista = re.findall(r'\d+', linhas[1]) if len(linhas) > 1 and linhas[1] else []
    texto4 = ''.join(texto4_lista) if texto4_lista else "InfoNaoEncontrada4"

    prefixo_moeda = ""
    if texto3 and not texto3.startswith(("Valor", "Liquido")):
        if any(char.isdigit() for char in texto3):
             prefixo_moeda = "R$ "

    titulo = f"{texto1} - {texto5} - {texto2} - {texto4} - {prefixo_moeda}{texto3}"
    titulo_sanitizado = re.sub(r'[\\/*?:"<>|]', '_', titulo)
    titulo_normalizado = re.sub(r'\s+', ' ', titulo_sanitizado).strip()
    titulo_final = re.sub(r' +', ' ', titulo_normalizado).strip()
    return titulo_final if titulo_final else "Nome_Arquivo_Invalido"

def extrair_dados_comprovante(texto_pagina):
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
        
        if nome:
            return f"{nome} - {cpf} - R$ {valor} - RECIBO"
        else:
            return f"{cpf} - R$ {valor} - RECIBO"
    return None

# ==========================================
# PROCESSAMENTO MISTO PÁGINA A PÁGINA
# ==========================================
def processar_pdf_misto_memoria(pdf_bytes, nome_origem, logger, holerites_dict, comprovantes_dict, doc_nao_classificadas):
    try:
        stream_plumber = io.BytesIO(pdf_bytes)
        doc_fitz = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        with pdfplumber.open(stream_plumber) as pdf_plumb:
            total_paginas = len(doc_fitz)
            logger.print(f"\n>>> Lendo arquivo: '{nome_origem}' ({total_paginas} páginas)")

            for i in range(total_paginas):
                # Extrai texto rápido com fitz para classificação
                texto_fitz = doc_fitz[i].get_text("text")
                tipo_pagina = classificar_texto_pagina(texto_fitz)

                if tipo_pagina == "HOLERITE":
                    # Holerite usa a lógica de texto do pdfplumber
                    texto_plumber = pdf_plumb.pages[i].extract_text() or ""
                    titulo = extrair_titulo_holerite(texto_plumber)
                    
                    nome_arquivo = f"{titulo}.pdf"
                    if len(nome_arquivo) > 200: nome_arquivo = nome_arquivo[:196] + ".pdf"
                    
                    nova_pagina_pdf = fitz.open()
                    nova_pagina_pdf.insert_pdf(doc_fitz, from_page=i, to_page=i)
                    holerites_dict[nome_arquivo] = nova_pagina_pdf.write()
                    nova_pagina_pdf.close()
                    
                    logger.print(f"  [Pág {i+1}] 🟢 HOLERITE -> Salvo como '{nome_arquivo}'")

                elif tipo_pagina == "COMPROVANTE":
                    # Comprovante usa o regex no texto do fitz
                    titulo = extrair_dados_comprovante(texto_fitz)
                    if titulo:
                        nome_arquivo = f"{titulo}.pdf"
                        
                        contador = 1
                        while nome_arquivo in comprovantes_dict:
                            nome_arquivo = f"{titulo}_{contador}.pdf"
                            contador += 1
                            
                        nova_pagina_pdf = fitz.open()
                        nova_pagina_pdf.insert_pdf(doc_fitz, from_page=i, to_page=i)
                        comprovantes_dict[nome_arquivo] = nova_pagina_pdf.write()
                        nova_pagina_pdf.close()
                        
                        logger.print(f"  [Pág {i+1}] 🔵 COMPROVANTE -> Salvo como '{nome_arquivo}'")
                    else:
                        logger.print(f"  [Pág {i+1}] ⚠ COMPROVANTE detectado, mas falhou ao extrair CPF/Valor. Enviando para NAO CLASSIFICADAS.")
                        # Se falhou em extrair dados essenciais, também vai para não classificadas
                        doc_nao_classificadas.insert_pdf(doc_fitz, from_page=i, to_page=i)

                else:
                    logger.print(f"  [Pág {i+1}] ⚪ DESCONHECIDO -> Adicionado ao arquivo de não classificadas.")
                    # Insere a página ignorada no documento unificado de não classificadas
                    doc_nao_classificadas.insert_pdf(doc_fitz, from_page=i, to_page=i)
                    
        doc_fitz.close()
    except Exception as e:
        logger.print(f"Erro ao processar arquivo {nome_origem}: {e}")

# ==========================================
# ETAPA 3: UNIÃO E AGREGAÇÃO
# ==========================================
def extrair_cpf_e_valor(nome_arquivo):
    padrao_cpf = r'(\d{3}\.\d{3}\.\d{3}-\d{2})'
    padrao_valor = r'R\$\s*([\d\.,]+,\d{2})'
    match_cpf = re.search(padrao_cpf, nome_arquivo)
    match_valor = re.search(padrao_valor, nome_arquivo)
    
    cpf_str = match_cpf.group(1) if match_cpf else None
    valor_float = None
    if match_valor:
        try:
            valor_float = float(match_valor.group(1).replace('.', '').replace(',', '.'))
        except:
            pass
    return cpf_str, valor_float

def unir_arquivos_memoria(holerites_dict, comprovantes_dict, logger):
    arquivos_finais = {}
    grupos_por_cpf = defaultdict(lambda: {'original': None, 'recibos': [], 'nome_original_completo': None})

    logger.print("\n--- Agrupando Arquivos por CPF ---")
    
    for nome, pdf_bytes in holerites_dict.items():
        cpf, valor = extrair_cpf_e_valor(nome)
        if cpf:
            grupos_por_cpf[cpf]['original'] = {'nome': nome, 'valor': valor, 'bytes': pdf_bytes}
            grupos_por_cpf[cpf]['nome_original_completo'] = nome

    for nome, pdf_bytes in comprovantes_dict.items():
        cpf, valor = extrair_cpf_e_valor(nome)
        if cpf:
            grupos_por_cpf[cpf]['recibos'].append({'nome': nome, 'valor': valor, 'bytes': pdf_bytes})

    logger.print("\n--- Iniciando o processo de união condicional ---")
    tolerancia = 0.01

    for cpf, dados in grupos_por_cpf.items():
        original = dados['original']
        recibos = dados['recibos']
        
        if not original:
            continue
            
        if not recibos:
            logger.print(f" [Aviso] Nenhum comprovante encontrado para CPF {cpf}. Holerite mantido isolado.")
            arquivos_finais[original['nome']] = original['bytes']
            continue

        valor_original = original['valor']
        uniao_realizada = False
        
        for recibo in recibos:
            if valor_original is not None and recibo['valor'] is not None and abs(recibo['valor'] - valor_original) < tolerancia:
                novo_nome = original['nome'].replace(".pdf", " - RECIBO_COMPROVANTE.pdf")
                
                writer = PdfWriter()
                writer.add_page(PdfReader(io.BytesIO(original['bytes'])).pages[0])
                writer.add_page(PdfReader(io.BytesIO(recibo['bytes'])).pages[0])
                
                out_stream = io.BytesIO()
                writer.write(out_stream)
                arquivos_finais[novo_nome] = out_stream.getvalue()
                
                logger.print(f" [SUCESSO] Unido match exato: {novo_nome}")
                uniao_realizada = True
                break

        if not uniao_realizada and valor_original is not None:
            melhor_combinacao = None
            for r_count in range(1, len(recibos) + 1):
                for combinacao in itertools.combinations(recibos, r_count):
                    soma = sum(r['valor'] for r in combinacao if r['valor'] is not None)
                    if abs(soma - valor_original) < tolerancia:
                        melhor_combinacao = combinacao
                        break
                if melhor_combinacao: break
                
            if melhor_combinacao:
                novo_nome = original['nome'].replace(".pdf", " - RECIBO_COMPROVANTE.pdf")
                writer = PdfWriter()
                writer.add_page(PdfReader(io.BytesIO(original['bytes'])).pages[0])
                
                recibos_ordenados = sorted(melhor_combinacao, key=lambda x: x['valor'] or 0)
                for rec in recibos_ordenados:
                    writer.add_page(PdfReader(io.BytesIO(rec['bytes'])).pages[0])
                    
                out_stream = io.BytesIO()
                writer.write(out_stream)
                arquivos_finais[novo_nome] = out_stream.getvalue()
                
                logger.print(f" [SUCESSO] Unido combinações: {novo_nome}")
                uniao_realizada = True

        if not uniao_realizada:
            logger.print(f" [Aviso] Sem combinações válidas para CPF {cpf}. Mantendo arquivo original.")
            arquivos_finais[original['nome']] = original['bytes']

    return arquivos_finais

# ==========================================
# INTERFACE STREAMLIT
# ==========================================
st.set_page_config(page_title="Processador de Holerites e Comprovantes", layout="wide")
st.title("📄 Processador e Unificador de PDFs")

# Modificamos o texto para avisar ao usuário sobre a nova funcionalidade
st.markdown("Faça o upload dos seus PDFs soltos **OU** suba um único arquivo **.zip** contendo todos eles juntos!")

# 1. Adicionamos a extensão "zip" nos tipos permitidos
uploaded_files = st.file_uploader("Selecione os arquivos (PDF ou ZIP)", type=["pdf", "zip"], accept_multiple_files=True)

if st.button("🚀 Iniciar Processamento"):
    if not uploaded_files:
        st.warning("Por favor, faça o upload de pelo menos um arquivo.")
    else:
        st.markdown("### 🖥️ Terminal de Processamento")
        app_logger = StreamlitLogger()
        
        holerites_separados = {}
        comprovantes_separados = {}
        doc_nao_classificadas = fitz.open()
        
        app_logger.print(">>> INICIANDO TRIAGEM E EXTRAÇÃO...")
        
        for file in uploaded_files:
            # ---------------------------------------------------------
            # NOVA LÓGICA: Descompactação de ZIP em memória
            # ---------------------------------------------------------
            if file.name.lower().endswith('.zip'):
                app_logger.print(f"\n📦 Abrindo arquivo ZIP: '{file.name}'")
                try:
                    # Lê o ZIP usando a biblioteca padrão do Python
                    with zipfile.ZipFile(file, 'r') as z:
                        for zip_info in z.infolist():
                            # Filtra para ler apenas PDFs (ignorando lixos de sistema como __MACOSX)
                            if zip_info.filename.lower().endswith('.pdf') and not zip_info.filename.startswith('__MACOSX'):
                                # Extrai o PDF em bytes e joga na nossa esteira
                                pdf_bytes = z.read(zip_info.filename)
                                # Usamos osplit para pegar só o nome final caso esteja dentro de pastas no zip
                                nome_limpo = zip_info.filename.split('/')[-1]
                                processar_pdf_misto_memoria(pdf_bytes, nome_limpo, app_logger, holerites_separados, comprovantes_separados, doc_nao_classificadas)
                except zipfile.BadZipFile:
                    app_logger.print(f"❌ Erro: O arquivo '{file.name}' parece não ser um ZIP válido.")
            
            # ---------------------------------------------------------
            # LÓGICA ANTIGA MANTIDA: Processamento de PDFs soltos
            # ---------------------------------------------------------
            elif file.name.lower().endswith('.pdf'):
                file_bytes = file.read()
                processar_pdf_misto_memoria(file_bytes, file.name, app_logger, holerites_separados, comprovantes_separados, doc_nao_classificadas)
            
        # União
        app_logger.print("\n>>> UNINDO HOLERITES E COMPROVANTES...")
        pdfs_finais = unir_arquivos_memoria(holerites_separados, comprovantes_separados, app_logger)
        
        # Verifica páginas não classificadas
        if len(doc_nao_classificadas) > 0:
            app_logger.print(f"\n>>> FORAM ENCONTRADAS {len(doc_nao_classificadas)} PÁGINA(S) NÃO CLASSIFICADA(S)!")
            pdfs_finais["NAO_CLASSIFICADAS.pdf"] = doc_nao_classificadas.write()
        
        doc_nao_classificadas.close()
        
        app_logger.print("\n>>> FINALIZADO! Preparando arquivo ZIP de saída...")

        # Geração do ZIP de saída
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for nome_arquivo, pdf_bytes in pdfs_finais.items():
                zip_file.writestr(nome_arquivo, pdf_bytes)
            zip_file.writestr("relatorio_processamento.txt", app_logger.log_text)

        st.success("Processamento concluído com sucesso!")
        st.download_button(
            label="⬇️ Baixar Arquivos Processados (.zip)",
            data=zip_buffer.getvalue(),
            file_name="arquivos_processados.zip",
            mime="application/zip",
            use_container_width=True
        )
