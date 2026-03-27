def extrair_titulo_holerite(texto):
    if not texto:
        return "Titulo_Pagina_Vazia_ou_Erro"

    # 1. Extrair Período (Mês_Ano)
    periodo = "MesAnoNaoEncontrado"
    # Procura pelo padrão DD/DDDD (ex: 02/2026)
    match_periodo = re.search(r'(\d{2}/\d{4})', texto)
    if match_periodo:
        periodo = match_periodo.group(1).replace('/', '_')

    # 2. Extrair CPF e formatar
    cpf = "CPFNaoEncontrado"
    # Procura por "CPF: " seguido de números ou pontuações
    match_cpf = re.search(r'CPF:\s*([\d\.\-]+)', texto, re.IGNORECASE)
    if match_cpf:
        cpf_bruto = match_cpf.group(1).strip()
        # Se veio só os 11 números, aplicamos a máscara
        if len(cpf_bruto) == 11 and cpf_bruto.isdigit():
            cpf = f"{cpf_bruto[:3]}.{cpf_bruto[3:6]}.{cpf_bruto[6:9]}-{cpf_bruto[9:]}"
        else:
            cpf = cpf_bruto # Mantém se já vier formatado

    # 3. Extrair Nome do Funcionário
    nome = "NomeNaoEncontrado"
    # Procura o texto que está entre "DATA:" e "CPF:" (onde o nome costuma ficar na base do documento)
    match_nome_base = re.search(r'DATA:\s*\n*(.*?)\n*CPF:', texto, re.IGNORECASE)
    if match_nome_base and match_nome_base.group(1).strip():
        nome = match_nome_base.group(1).strip()
    else:
        # Fallback: Procura o nome logo no início após "Funcionário:"
        match_nome_topo = re.search(r'Funcionário:\s*.*?\n(.*?)\n', texto, re.IGNORECASE)
        if match_nome_topo:
             nome = match_nome_topo.group(1).strip()

    # 4. Extrair Salário Líquido
    valor = "ValorNaoEncontrado"
    # Procura por "SALÁRIO LÍQUIDO:" e pega a primeira sequência numérica (com vírgula) após isso
    match_valor = re.search(r'SALÁRIO LÍQUIDO:[^\d]*([\d\.]+,[\d]{2})', texto, re.IGNORECASE)
    if match_valor:
        valor = match_valor.group(1)

    # 5. Montar o título final e sanitizar
    titulo = f"{nome} - {cpf} - {periodo} - R$ {valor}"
    
    # Remove espaços duplos extras, quebras de linha e caracteres inválidos para nomes de arquivos
    titulo_sanitizado = re.sub(r'[\\/*?:"<>|]', '_', titulo)
    titulo_limpo = re.sub(r'\s+', ' ', titulo_sanitizado).strip()
    
    return titulo_limpo if titulo_limpo else "Nome_Arquivo_Invalido"
