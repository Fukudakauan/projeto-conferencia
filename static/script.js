function atualizarTabela(codigo, produto) {
    const linha = document.getElementById(`linha-${codigo}`);
    if (linha) {
        const celulas = linha.getElementsByTagName('td');
        celulas[3].innerText = produto.quant_conferida;

        if (produto.quant_conferida == produto.quant_esperada) {
            linha.className = 'linha-ok';
        } else if (produto.quant_conferida < produto.quant_esperada) {
            linha.className = 'linha-faltando';
        } else {
            linha.className = 'linha-sobrando';
        }
    }
}

function mostrarMensagem(texto, sucesso) {
    const mensagem = document.getElementById('mensagem');
    mensagem.innerText = texto;
    mensagem.style.color = sucesso ? 'lime' : 'red';
}

function enviarCodigo(codigo) {
    fetch('/bipar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ codigo: codigo })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'ok') {
            atualizarTabela(codigo, data.produto);
            mostrarMensagem('✅ Produto conferido com sucesso!', true);
        } else {
            mostrarMensagem(data.mensagem || "Produto não encontrado!", false);
        }
    });
}

function enviarCodigoManual(codigoProduto) {
    const quantidade = prompt("Digite a quantidade conferida:");

    if (quantidade === null || quantidade.trim() === "" || isNaN(quantidade)) {
        mostrarMensagem("Quantidade inválida!", false);
        return;
    }

    const quantidadeInt = parseInt(quantidade);

    fetch('/bipar_manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ produto: codigoProduto, quantidade: quantidadeInt })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'ok') {
            atualizarTabela(data.codigo_barra, data.produto);

            const mensagem = `✅ Produto conferido manualmente!\n📦 Produto: ${data.produto.descricao}\n🔢 Código: ${data.codigo_barra}\n📥 Quantidade: ${quantidadeInt}`;
            mostrarMensagem(mensagem, true);

            const input = document.getElementById('codigo-manual');
            input.value = '';
            requestAnimationFrame(() => {
            input.focus();
            input.select();
                            });

        } else {
            mostrarMensagem(data.mensagem || "Produto não encontrado!", false);
        }
    });
}

document.addEventListener('DOMContentLoaded', function () {
    const btnFinalizar = document.getElementById('btn-finalizar');
    const btnBuscarManual = document.getElementById('btn-buscar-manual');
    const btnApagar = document.getElementById('btn-apagar');
    const inputCodigo = document.getElementById('codigo-manual');

btnFinalizar.addEventListener('click', () => {
    if (confirm('Deseja finalizar a conferência?')) {
        fetch('/finalizar', { method: 'POST' })
            .then(response => {
                if (!response.ok) throw new Error("Erro ao gerar relatório.");
                return response.blob(); // recebe o PDF
            })
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'relatorio_conferencia.pdf';
                document.body.appendChild(a);
                a.click();
                a.remove();
                mostrarMensagem("✅ Relatório baixado com sucesso!", true);
                setTimeout(() => location.reload(), 1500);
            })
            .catch(() => {
                mostrarMensagem("❌ Erro ao baixar o relatório!", false);
            });
    }
});

    btnBuscarManual.addEventListener('click', () => {
        const codigo = inputCodigo.value.trim();
        if (codigo) {
            enviarCodigoManual(codigo);
        } else {
            mostrarMensagem("Digite um código para buscar!", false);
        }
    });

    btnApagar.addEventListener('click', () => {
        if (confirm('⚠️ Tem certeza que deseja apagar toda a conferência?')) {
            fetch('/apagar', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'ok') {
                        alert('Conferência apagada com sucesso!');
                        location.reload();
                    }
                });
        }
    });

    inputCodigo.addEventListener('keypress', function (event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            const codigo = inputCodigo.value.trim();
            if (codigo) {
                enviarCodigoManual(codigo);
            } else {
                mostrarMensagem("Digite um código para buscar!", false);
            }
        }
    });
});
