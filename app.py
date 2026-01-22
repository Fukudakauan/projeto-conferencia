from flask import Flask, render_template, request, redirect, url_for, jsonify, session, send_file
import pandas as pd
import xml.etree.ElementTree as ET
import PyPDF2
import re
import os
import hashlib
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
import socket
import os
import psycopg2
from psycopg2.extras import RealDictCursor

# ================= Banco PostgreSQL (único) =================

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = True

if not DATABASE_URL:
    raise RuntimeError(
        "ERRO CRÍTICO: DATABASE_URL não definida. "
        "Este sistema funciona APENAS com PostgreSQL."
    )

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def ph():
    return "%s"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev_key")

UPLOAD_FOLDER = '/data/uploads'
RELATORIOS_FOLDER = '/data/relatorios'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


# ================= IP local =================

def obter_ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


# ================= Inicialização do storage =================

def garantir_pastas():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(RELATORIOS_FOLDER, exist_ok=True)
    os.makedirs('/data/ssl', exist_ok=True)


# ================= Banco =================
def criar_banco():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            tipo TEXT NOT NULL
        );
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            id SERIAL PRIMARY KEY,
            codigo TEXT,
            descricao TEXT,
            quant_esperada INTEGER,
            quant_conferida INTEGER,
            produto TEXT,
            loja_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE
        );
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_produtos_loja_codigo
        ON produtos(loja_id, codigo);
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_produtos_loja_produto
        ON produtos(loja_id, produto);
    """)

    # Admin padrão
    c.execute("SELECT 1 FROM usuarios WHERE usuario = %s", ('admin',))
    if not c.fetchone():
        senha_hash = hashlib.sha256('110609.k'.encode()).hexdigest()
        c.execute(
            "INSERT INTO usuarios (usuario, senha, tipo) VALUES (%s, %s, %s)",
            ('admin', senha_hash, 'admin')
        )

    conn.commit()
    c.close()
    conn.close()


# ================= Auth =================

def verificar_login(usuario, senha):
    senha_hash = hashlib.sha256(senha.encode()).hexdigest()
    conn = get_conn()
    c = conn.cursor()
    P = ph()

    c.execute(
        f"SELECT id, tipo FROM usuarios WHERE usuario = {P} AND senha = {P}",
        (usuario, senha_hash)
    )
    resultado = c.fetchone()

    c.close()
    conn.close()
    return resultado if resultado else None


# ================= Importação =================

def importar_arquivo(caminho_arquivo, loja_id):
    import unicodedata

    def _norm(txt):
        if not isinstance(txt, str):
            txt = str(txt) if txt is not None else ""
        txt = txt.strip().lower()
        txt = ''.join(
            c for c in unicodedata.normalize('NFD', txt)
            if unicodedata.category(c) != 'Mn'
        )
        txt = re.sub(r'\s+', ' ', txt)
        return txt

    def _only_digits(s):
        if s is None:
            return ""
        s = str(s).strip()

        # remove .0 do excel
        if s.endswith(".0"):
            s = s[:-2]

        # trata notação científica (ex: 7.89123E+12)
        if "e" in s.lower():
            try:
                s = f"{int(float(s))}"
            except:
                pass

        return re.sub(r"\D", "", s)

    def detectar_colunas(df):
        cols = list(df.columns)
        norm_map = {_norm(c): c for c in cols}
        norm_cols = list(norm_map.keys())

        keys_ean  = ['ean','gtin','barra','barcode','codbarra','codigo de barras','cod de barras',
                     'cod barra','codbarras','cod bar','ean13','gtin14','gtin-14']
        keys_prod = ['produto','prod','ref','referencia','codigo','cod','cod prod','cod produto',
                     'codinterno','codigo interno']
        keys_desc = ['descricao','descr','produto descricao','nome','item']
        keys_qtd  = ['quant','qtd','qtde','quantidade']

        def pick(keys, avoid=None):
            for k in keys:
                for nc in norm_cols:
                    if k in nc and (not avoid or nc != avoid):
                        return norm_map[nc]
            return None

        ean_col   = pick(keys_ean)
        prod_col  = pick(keys_prod, avoid=_norm(ean_col) if ean_col else None)
        desc_col  = pick(keys_desc)
        quant_col = pick(keys_qtd)
        return ean_col, prod_col, desc_col, quant_col

    ext = caminho_arquivo.lower().split('.')[-1]
    dados_importados = []
    df = None

    try:
        # ====== LEITURA ======
        if ext in ['xlsx', 'xls']:
            df = pd.read_excel(caminho_arquivo, dtype=str)

        elif ext == 'csv':
            tried = []
            for enc, sep in [('utf-8',';'), ('utf-8',','), ('latin-1',';'), ('latin-1',',')]:
                try:
                    df = pd.read_csv(caminho_arquivo, encoding=enc, sep=sep, dtype=str)
                    break
                except Exception as e:
                    tried.append(f"{enc}/{sep}: {e}")
            if df is None:
                return "Erro ao abrir CSV: " + " | ".join(tried)

        elif ext == 'xml':
            tree = ET.parse(caminho_arquivo)
            root = tree.getroot()
            ns = {'ns': 'http://www.portalfiscal.inf.br/nfe'}

            for idx, det in enumerate(root.findall('.//ns:det', ns)):
                cProd = det.findtext('.//ns:cProd', namespaces=ns) or ""
                cEAN  = det.findtext('.//ns:cEAN', namespaces=ns) or ""
                xProd = det.findtext('.//ns:xProd', namespaces=ns) or "SEM DESCRIÇÃO"
                qCom  = det.findtext('.//ns:qCom', namespaces=ns) or "0"

                ean_val = _only_digits(cEAN)
                if ean_val and cEAN.strip().upper() != 'SEM GTIN':
                    codigo = ean_val              # EAN
                else:
                    codigo = cProd.strip() or f"AUTOXML{idx}"

                produto_ref = cProd.strip() or codigo  # ref curta
                try:
                    quantidade = int(float(qCom.replace(',', '.')))
                except:
                    quantidade = 0

                dados_importados.append((codigo, xProd.strip(), quantidade, produto_ref))

        elif ext == 'pdf':
            with open(caminho_arquivo, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                texto = "\n".join(p.extract_text() for p in reader.pages if p.extract_text())

            linhas = texto.split("\n")
            ignorar = ["remetente", "razão", "recebemos", "danfe", "data", "nota", "chave"]

            for idx, linha in enumerate(linhas):
                partes = linha.split()
                if not partes:
                    continue
                linha_lower = linha.lower()
                if any(p in linha_lower for p in ignorar):
                    continue
                if len(partes) >= 3 and partes[0].isdigit():
                    prod_ref = partes[0].strip()
                    descricao = " ".join(partes[1:-1]).strip() or "SEM DESCRIÇÃO"
                    try:
                        quantidade = int(float(partes[-1].replace(',', '.')))
                    except:
                        quantidade = 0
                    dados_importados.append((prod_ref, descricao, quantidade, prod_ref))

        else:
            return f"Formato {ext} não é suportado."

        # ====== TRATAMENTO DO DF (XLSX/CSV) ======
        if df is not None:
            ean_col, prod_col, desc_col, quant_col = detectar_colunas(df)
            if not quant_col or not (ean_col or prod_col):
                return f"Erro: não encontrei colunas de quantidade e de código. Colunas disponíveis: {list(df.columns)}"

            for i, row in df.iterrows():
                codigo = None        # EAN (barras)
                produto_ref = None   # código curto

                if ean_col and pd.notna(row.get(ean_col)):
                    codigo = _only_digits(row.get(ean_col)) or None

                if prod_col and pd.notna(row.get(prod_col)):
                    produto_ref = str(row.get(prod_col)).strip()

                # se não tiver nenhum dos dois
                if not codigo and not produto_ref:
                    produto_ref = f"AUTO{i}"
                    codigo = produto_ref

                # se não tiver EAN, usa ref como codigo também
                if not codigo and produto_ref:
                    codigo = produto_ref

                descricao = "SEM DESCRIÇÃO"
                if desc_col and pd.notna(row.get(desc_col)):
                    descricao = str(row.get(desc_col)).strip()

                try:
                    quantidade = int(float(str(row.get(quant_col)).replace(',', '.')))
                except:
                    quantidade = 0

                dados_importados.append((codigo, descricao, quantidade, produto_ref or codigo))

        # ====== CONSOLIDAÇÃO ======
        produtos_unicos = {}
        for codigo, descricao, quantidade, produto_ref in dados_importados:
            key = (codigo or produto_ref or "").strip()
            if key in produtos_unicos:
                produtos_unicos[key]['quantidade'] += quantidade
            else:
                produtos_unicos[key] = {
                    'codigo': (codigo or produto_ref or "").strip(),
                    'descricao': descricao,
                    'quantidade': quantidade,
                    'produto': (produto_ref or codigo or "").strip()
                }

        dados_finais = [
            (p['codigo'], p['descricao'], p['quantidade'], p['produto'])
            for p in produtos_unicos.values()
        ]

        # ====== SALVAR NO BANCO ======
        conn = get_conn()
        c = conn.cursor()
        P = ph()

        c.execute(f"DELETE FROM produtos WHERE loja_id = {P}", (loja_id,))
        for codigo, descricao, quantidade, produto_ref in dados_finais:
            c.execute(f"""
                INSERT INTO produtos (codigo, descricao, quant_esperada, quant_conferida, produto, loja_id)
                VALUES ({P}, {P}, {P}, 0, {P}, {P})
            """, (codigo, descricao, quantidade, produto_ref, loja_id))

        conn.commit()
        c.close()
        conn.close()

        return f"[OK] Importação concluída com {len(dados_finais)} produtos."

    except Exception as e:
        return f"Erro inesperado: {e}"


# ================= Produtos =================

def buscar_produtos():
    if 'usuario_id' not in session:
        return {}

    conn = get_conn()
    c = conn.cursor()
    P = ph()

    c.execute(f"""
        SELECT codigo, descricao, quant_esperada, quant_conferida, produto
        FROM produtos
        WHERE loja_id = {P}
    """, (session['usuario_id'],))
    dados = c.fetchall()

    c.close()
    conn.close()

    produtos = {}
    for codigo, descricao, quant_esperada, quant_conferida, produto in dados:
        produtos[codigo] = {
            'descricao': descricao,
            'quant_esperada': quant_esperada,
            'quant_conferida': quant_conferida,
            'produto': produto
        }
    return produtos


def zerar_conferencia(loja_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE produtos SET quant_conferida = 0 WHERE loja_id = %s", (loja_id,))
    conn.commit()
    c.close()
    conn.close()


# ================= Rotas =================

@app.route('/')
def index():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form['usuario']
        senha = request.form['senha']
        resultado = verificar_login(usuario, senha)
        if resultado:
            session['usuario_id'] = resultado[0]
            session['usuario_nome'] = usuario
            session['tipo'] = resultado[1]
            if resultado[1] == 'admin':
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('conferencia_loja'))
        else:
            return render_template('login.html', erro='Usuário ou senha inválidos.')
    return render_template('login.html')


@app.route('/upload', methods=['POST'])
def upload():
    if 'usuario_id' not in session or session.get('tipo') != 'loja':
        return redirect(url_for('login'))

    if 'planilha' not in request.files:
        return render_template('index.html', erro="Nenhum arquivo enviado.")

    file = request.files['planilha']
    if file.filename == '':
        return render_template('index.html', erro="Nenhum arquivo selecionado.")

    try:
        caminho = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(caminho)
        importar_arquivo(caminho, session['usuario_id'])
        return redirect(url_for('conferencia_loja'))
    except Exception as e:
        return render_template('index.html', erro=str(e))


@app.route('/bipar', methods=['POST'])
def bipar():
    if 'usuario_id' not in session:
        return jsonify({'status': 'erro', 'mensagem': 'Não autenticado'})

    from re import sub as _resub

    data = request.json or {}
    entrada_raw = str(data.get('codigo') or data.get('entrada') or '').strip()
    quantidade = int(data.get('quantidade', 1) or 1)

    entrada_num = _resub(r'\D', '', entrada_raw)
    entrada_txt = entrada_raw
    loja_id = session['usuario_id']

    conn = get_conn()
    c = conn.cursor()
    P = ph()

    c.execute(f"""
        SELECT codigo, descricao, quant_esperada, quant_conferida, produto
        FROM produtos
        WHERE loja_id = {P} AND (produto = {P} OR codigo = {P})
        LIMIT 1
    """, (loja_id, entrada_txt, entrada_txt))
    row = c.fetchone()

    if not row and entrada_num:
        c.execute(f"""
            SELECT codigo, descricao, quant_esperada, quant_conferida, produto
            FROM produtos
            WHERE loja_id = {P} AND codigo = {P}
            LIMIT 1
        """, (loja_id, entrada_num))
        row = c.fetchone()

    if not row:
        c.close()
        conn.close()
        return jsonify({'status': 'erro', 'mensagem': 'Produto não encontrado!'})

    codigo_pk = row[0]

    c.execute(f"""
        UPDATE produtos
        SET quant_conferida = quant_conferida + {P}
        WHERE loja_id = {P} AND (codigo = {P} OR produto = {P})
    """, (quantidade, loja_id, codigo_pk, row[4]))

    conn.commit()
    c.close()
    conn.close()

    produto_resp = {
        'descricao': row[1],
        'quant_esperada': row[2],
        'quant_conferida': row[3] + quantidade,
        'produto': row[4]
    }
    return jsonify({'status': 'ok', 'produto': produto_resp, 'codigo_barra': row[0]})


@app.route('/bipar_manual', methods=['POST'])
def bipar_manual():
    if 'usuario_id' not in session:
        return jsonify({'status': 'erro', 'mensagem': 'Não autenticado'})

    data = request.json or {}
    entrada = str(data.get('produto') or '').strip()
    quantidade = int(data.get('quantidade', 1) or 1)

    conn = get_conn()
    c = conn.cursor()
    P = ph()

    c.execute(f"""
        SELECT codigo, descricao, quant_esperada, quant_conferida, produto
        FROM produtos
        WHERE (produto = {P} OR codigo = {P}) AND loja_id = {P}
        LIMIT 1
    """, (entrada, entrada, session['usuario_id']))
    row = c.fetchone()

    if not row:
        c.close()
        conn.close()
        return jsonify({'status': 'erro', 'mensagem': 'Produto não encontrado!'})

    c.execute(f"""
        UPDATE produtos
        SET quant_conferida = quant_conferida + {P}
        WHERE codigo = {P} AND loja_id = {P}
    """, (quantidade, row[0], session['usuario_id']))

    conn.commit()
    c.close()
    conn.close()

    produto = {
        'descricao': row[1],
        'quant_esperada': row[2],
        'quant_conferida': row[3] + quantidade,
        'produto': row[4]
    }
    return jsonify({'status': 'ok', 'produto': produto, 'codigo_barra': row[0]})


@app.route('/apagar', methods=['POST'])
def apagar():
    if 'usuario_id' not in session or session.get('tipo') != 'loja':
        return jsonify({'status': 'erro', 'mensagem': 'Não autorizado'})

    conn = get_conn()
    c = conn.cursor()
    P = ph()
    c.execute(f"DELETE FROM produtos WHERE loja_id = {P}", (session['usuario_id'],))
    conn.commit()
    c.close()
    conn.close()

    return jsonify({'status': 'ok'})


@app.route('/relatorio')
def gerar_relatorio():
    if 'usuario_id' not in session or session.get('tipo') != 'loja':
        return redirect(url_for('login'))

    loja_id = session['usuario_id']
    tipo = request.args.get('tipo', 'completo')

    conn = get_conn()
    c = conn.cursor()
    P = ph()

    c.execute(f"""
        SELECT produto, descricao, quant_esperada, quant_conferida
        FROM produtos
        WHERE loja_id = {P}
    """, (loja_id,))
    dados = c.fetchall()

    c.close()
    conn.close()

    if tipo == 'faltas':
        dados = [d for d in dados if d[3] < d[2]]
    elif tipo == 'sobras':
        dados = [d for d in dados if d[3] > d[2]]
    elif tipo == 'sobras_faltas':
        dados = [d for d in dados if d[3] != d[2]]

    agora = datetime.now().strftime('%d_%m_%Y_%H_%M_%S')
    relatorio_nome = os.path.join(RELATORIOS_FOLDER, f"relatorio_{tipo}_loja_{loja_id}_{agora}.pdf")

    c_pdf = canvas.Canvas(relatorio_nome, pagesize=A4)
    width, height = A4

    c_pdf.setFont("Helvetica-Bold", 18)
    c_pdf.drawCentredString(width / 2, height - 50, f"Relatório: {tipo.capitalize()}")

    c_pdf.setFont("Helvetica-Bold", 12)
    c_pdf.drawString(30, height - 100, "Produto")
    c_pdf.drawString(130, height - 100, "Descrição")
    c_pdf.drawString(300, height - 100, "Esperado")
    c_pdf.drawString(370, height - 100, "Conferido")
    c_pdf.drawString(450, height - 100, "Status")
    c_pdf.line(25, height - 110, width - 25, height - 110)

    y = height - 130
    for produto, descricao, esperado, conferido in dados:
        if conferido == esperado:
            status = "OK"
            cor_fundo = colors.lightgreen
            cor_texto = colors.black
        elif conferido < esperado:
            status = "FALTANDO"
            cor_fundo = colors.salmon
            cor_texto = colors.white
        else:
            status = "SOBRANDO"
            cor_fundo = colors.yellow
            cor_texto = colors.black

        if y < 50:
            c_pdf.showPage()
            y = height - 80

        c_pdf.setFillColor(cor_fundo)
        c_pdf.rect(25, y - 4, width - 50, 18, fill=True, stroke=False)

        c_pdf.setFillColor(cor_texto)
        c_pdf.setFont("Helvetica", 10)
        c_pdf.drawString(30, y, str(produto))
        c_pdf.drawString(130, y, str(descricao)[:30])
        c_pdf.drawString(300, y, str(esperado))
        c_pdf.drawString(370, y, str(conferido))
        c_pdf.drawString(450, y, status)

        y -= 20

    c_pdf.save()
    return send_file(relatorio_nome, as_attachment=True)


@app.route('/admin')
def admin():
    if 'usuario_id' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    conn = get_conn()
    c = conn.cursor()
    P = ph()

    c.execute(f"SELECT id, usuario FROM usuarios WHERE tipo = {P}", ('loja',))
    lojas = c.fetchall()

    c.close()
    conn.close()
    return render_template('admin.html', lojas=lojas)

@app.route('/admin/cadastrar', methods=['POST'])
def cadastrar_loja():
    if 'usuario_id' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    usuario = request.form['usuario']
    senha = request.form['senha']
    senha_hash = hashlib.sha256(senha.encode()).hexdigest()

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute("""
            INSERT INTO usuarios (usuario, senha, tipo)
            VALUES (%s, %s, %s)
            ON CONFLICT (usuario) DO NOTHING
        """, (usuario, senha_hash, 'loja'))

        conn.commit()
    except Exception as e:
        conn.rollback()
        c.close()
        conn.close()
        return render_template('admin.html', erro=f'Erro ao cadastrar loja: {e}', lojas=[])

    c.close()
    conn.close()
    return redirect(url_for('admin'))


@app.route('/conferencia')
def conferencia_loja():
    if 'usuario_id' not in session or session.get('tipo') != 'loja':
        return redirect(url_for('login'))

    loja_id = session['usuario_id']

    conn = get_conn()
    c = conn.cursor()
    P = ph()
    c.execute(f"""
        SELECT codigo, descricao, quant_esperada, quant_conferida, produto
        FROM produtos
        WHERE loja_id = {P}
    """, (loja_id,))
    dados = c.fetchall()
    c.close()
    conn.close()

    produtos = {}
    for codigo, descricao, quant_esperada, quant_conferida, produto in dados:
        produtos[codigo] = {
            'descricao': descricao,
            'quant_esperada': quant_esperada,
            'quant_conferida': quant_conferida,
            'produto': produto
        }

    return render_template(
        'index.html',
        produtos=produtos,
        ip=obter_ip_local(),
        nome_loja=session.get('usuario_nome', 'Loja')
    )


@app.route('/admin/deletar', methods=['POST'])
def deletar_loja():
    if 'usuario_id' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    loja_id = request.form['id']
    conn = get_conn()
    c = conn.cursor()
    P = ph()

    c.execute(f"DELETE FROM produtos WHERE loja_id = {P}", (loja_id,))
    c.execute(f"DELETE FROM usuarios WHERE id = {P} AND tipo = {P}", (loja_id, 'loja'))

    conn.commit()
    c.close()
    conn.close()

    return redirect(url_for('admin'))


# ================= Boot (Gunicorn/Fly e local) =================

garantir_pastas()
criar_banco()

if __name__ == '__main__':
    if not os.path.exists('/data/uploads'):
        os.makedirs('/data/uploads')
    if not os.path.exists('/data/relatorios'):
        os.makedirs('/data/relatorios')
    if not os.path.exists('/data/ssl'):
        os.makedirs('/data/ssl')

    criar_banco()

    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

