$.datepicker.setDefaults($.datepicker.regional["es"]);
$.datepicker.setDefaults({
	maxDate: "+0d",
	changeMonth: true,
	changeYear: true
});

const DATE_FORMAT = $.datepicker.regional["es"].dateFormat;

const textareaToolbar = "[['h1','h2','h3','p'],['bold','italics']," +
	"['justifyLeft', 'justifyCenter', 'justifyRight', 'indent', 'outdent']]";

const fechaOptions = {
	"icons" : {
		"next" : "fa fa-angle-right",
		"previous" : "fa fa-angle-left",
		"up" : "fa fa-angle-up",
		"down" : "fa fa-angle-down"
	},
	"format" : "YY/MM/DD"
};

var generateParagraph = function(child, fontSize, bold, italics) {
	var childParagraph = {};
	if (!child.nodeName || child.nodeName == '#text') {
		childParagraph.text = child.nodeValue + "\n";
		childParagraph.fontSize = fontSize;
		childParagraph.bold = bold;
		childParagraph.italics = italics;
		childParagraph.style = {lineHeight: 1.25};
		childParagraph.alignment = "justify";
	} else if (child.childNodes.length > 0) {
		if (child.nodeName.toUpperCase() == 'B') {
			childParagraph.text = generateText(child.childNodes,  fontSize, true, italics);
		} else if (child.nodeName.toUpperCase() == 'I') {
			childParagraph.text = generateText(child.childNodes,  fontSize, bold, true);
		} else if (child.nodeName.toUpperCase() == 'P') {
			childParagraph.text = generateText(child.childNodes,  fontSize, bold, italics);
		} else if (child.nodeName.toUpperCase() == 'SPAN') {
			if(child.attributes.length>0){
				var style = child.attributes[0].value;
				var attr = style.split(';');
				for(s in attr){
					var param = attr[s];
					if(param.includes('font-size')){
						fSize = parseFloat(param.replace(/[a-z-:]/gi,'').trim());
						childParagraph.fontSize = fSize;
					}else if(param.includes('color')){
						var color = param.replace('color: ','').trim();
						childParagraph.color = color;
					}
				}
			}
			childParagraph.text = generateText(child.childNodes,  fontSize, bold, italics);
		} 
		else {
			childParagraph.text = "\n";
			childParagraph.fontSize = fontSize;
		}
	} else {
		childParagraph.text = "\n";
		childParagraph.fontSize = fontSize;
	}
	return childParagraph;
};

var generateText = function(childNodes, fontSize, bold, italics){
	var text = [];
	for(s in childNodes){
		 if (childNodes.hasOwnProperty(s)) {
			 text.push(generateParagraph(childNodes[s], fontSize, bold, italics))
		 }
	}
	return text;
};

var parseField = function(title, parser, docDefinition, field, defaultText) {
	
	if (title) {
		var titleHeader = {};
		titleHeader.table = {
			widths : ['*'],
			body : [[
				{
				text : title,
				style : 'filledHeader'
				}
			]]

		};
		docDefinition.content.push(titleHeader);
	}
	
	if (field && field.trim()) {
		var doc = parser.parseFromString(field, "text/html");
		var body = doc.getElementsByTagName("body")[0];

		for (var c = 0; c < body.childNodes.length; c++) {
			var child = body.childNodes[c];
			var paragraph = {};
			var fontSize = 10;
			if (!child.nodeName || child.nodeName.toUpperCase() == 'P') {
				fontSize = 10;
			} else if (child.nodeName.toUpperCase() == 'H3') {
				fontSize = 11;
			} else if (child.nodeName.toUpperCase() == 'H2') {
				fontSize = 12;
			} else if (child.nodeName.toUpperCase() == 'H1') {
				fontSize = 14;
			}
			if (child.childNodes && child.childNodes.length > 0 && 
				(
					!child.childNodes[0].nodeName || 
					(child.childNodes[0].nodeName.toUpperCase() != 'TITLE' && child.childNodes[0].nodeName.toUpperCase() != 'BR')
				)) {
				paragraph.text = [];
				for (var par = 0; par < child.childNodes.length; par++) {
					var result = generateParagraph(child.childNodes[par], fontSize, false, false);
					if (result.text) {
						paragraph.text.push(result);
					}
				}
			} else if (child.nodeName == '#text') {
				paragraph.text = child.nodeValue + "\n";
				paragraph.fontSize = fontSize;
				paragraph.alignment = "justify";
			} else {
				paragraph.text = "\n";
				paragraph.fontSize = fontSize;
				paragraph.alignment = "justify";
			}
			docDefinition.content.push(paragraph);
		}
		docDefinition.content.push({text: "\n"});
	}else {
		docDefinition.content.push({text: defaultText+"\n\n", fontSize:10});
	}
};

var parseArticulos = function(vm, parser, docDefinition, articulos) {
	var titleHeader = {};
	titleHeader.table = {
		widths : ['*'],
		body : [[
			{
			text : 'Artículos',
			style : 'filledHeader'
			}
		]]

	};
	docDefinition.content.push(titleHeader);
	if (articulos) {

		var tableBody = [ [ {text:'Normativa', fontSize: 10}, {text:'Artículo', fontSize: 10}] ];
		for (var i = 0; i < articulos.length; i++) {
			var pronArt = articulos[i];
			tableBody.push([{text:pronArt.articulo.tituloBO.cuerpoNormativo.nombre, fontSize: 10},
							{text:pronArt.articulo.nombre + " " + pronArt.nota , fontSize: 10}]);
		}

		docDefinition.content.push({
			layout : 'lightHorizontalLines',
			table : {
				headerRows : 1,
				widths : [ '*', '*'],
				body : tableBody
			}
		});
		
		docDefinition.content.push({text: "\n"});
	}
};

var parsePronunciamiento = function(app, vm, parser, docDefinition) {
	
	var pronunciamientoHeader = {};
	pronunciamientoHeader.table = {
		widths : ['*'],
		body : [[
			{
			text : 'Pronunciamiento',
			style : 'filledHeader'
			}
		]]

	};
	
	docDefinition.content.push(pronunciamientoHeader);
	
	if (vm.downloadUrl && app == "Intranet") {

		
		docDefinition.content.push({
			text: [
				"Puede descargar el documento en el siguiente link:\n",
				{
					text: vm.downloadUrl, 
					link: vm.downloadUrl,
					color: "blue",
					decoration: 'underline'
				}
			],
			fontSize: 10
		});
		
	} else {
		docDefinition.content.push({
			text: [
				"Para revisar el pronunciamiento ingresar al sistema ACJ:\n ",
				{
					text: vm.host+"acjui/"+app.toLowerCase(), 
					link: vm.host+"acjui/"+app.toLowerCase(),
					color: "blue",
					decoration: 'underline'
				}
			],
			fontSize: 10
		});
	}
	docDefinition.content.push({text: "\n"});

};

var makeHeader = function(app, vm, docDefinition) {
	var siiImageUrl = 'data:image/png;base64,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';
	var logo = {
		image: siiImageUrl,
		width: 100
	}
	var urlPron = {
		text: vm.host+"acjui/"+app.toLowerCase()+"/#/pronunciamiento/"+vm.data.pronunciamiento.id, 
		link: vm.host+"acjui/"+app.toLowerCase()+"/#/pronunciamiento/"+vm.data.pronunciamiento.id,
		color: "blue",
		decoration: 'underline'
	}
	docDefinition.content.push({
		columns: [
			logo,
			{
				stack: [
					"Administrador de Contenidos de Jurisprudencia",
					{ 
						text: vm.data.pronunciamiento.tipoCodigo.nombre + " " + vm.data.pronunciamiento.codigoPronunciamiento, 
						fontSize: 14, 
						bold: true
					},
					urlPron
				]
			}
		]
	});
	docDefinition.content.push("\n");
	docDefinition.content.push({canvas: [{ type: 'line', x1: 0, y1: 5, x2: 515, y2: 5, lineWidth: 1 }]});
	docDefinition.content.push("\n");
};

var htmlToPdf = function(app, vm, callback) {
	
	var parser = new DOMParser();
    var styles= {
        filledHeader: {
            fontSize: 14,
            color: 'black',
            fillColor: '#93C6F1',
            alignment: 'left'
        }
    }

	var docDefinition = {content: [], styles: styles};
    
    makeHeader(app, vm, docDefinition);
    
    var administrativa = vm.tipoInstancia.administrativa;
    
    if (administrativa) {
    		docDefinition.content.push({
    			text: "Los siguientes criterios administrativos no tienen fuerza vinculante para los funcionarios del Servicio, por no corresponder a una interpretación oficial de las normas tributarias. En consecuencia, tienen una finalidad meramente orientativa respecto de los temas sobre los que tratan, sin que puedan reemplazar las instrucciones e interpretaciones contenidas en los actos firmados por el Director.",
    			fontSize: 8,
    			color: "#777"
    		});
    		docDefinition.content.push("\n");
    }
	
	parseField(null, parser, docDefinition, vm.data.pronunciamiento.contenido["resumen" + app], "");
	parseField(administrativa ? "Entidad Emisora" : "Tribunal", parser, docDefinition, vm.data.pronunciamiento.instancia.nombre, "");
	var date = new Date(vm.data.pronunciamiento.fecha)
	var day = date.getDate()+1;
	var monthIndex = date.getMonth()+1;
	var year = date.getFullYear();
	parseField("Fecha", parser, docDefinition, day+"-"+monthIndex+"-"+year);
	parseField("Tipo Pronunciamiento", parser, docDefinition, vm.data.pronunciamiento.tipoPronunciamiento.nombre, "");
	
	if (!administrativa && app == 'Intranet') {
		parseField("Decisión", parser, docDefinition, vm.data.pronunciamiento.decision.nombre);
		parseField("Resultado", parser, docDefinition, vm.data.pronunciamiento.resultado.texto);
	}
		
	if (!administrativa) {
		parseField("RUC", parser, docDefinition, vm.data.pronunciamiento.ruc, "No definido");
		parseField("Partes", parser, docDefinition, vm.data.pronunciamiento.partes, "Sin partes");
	}
	
	parseArticulos(vm, parser, docDefinition, vm.data.pronunciamiento.pronunciamientosArticulos);
	
	if(vm.tipoInstancia.extracto.id!=3){
		parseField("Extracto", parser, docDefinition, vm.data.pronunciamiento.contenido["extracto" + app], "Sin extracto");
	}
	if(vm.tipoInstancia.pronunciamiento.id!=3){
		parsePronunciamiento(app, vm, parser, docDefinition);
	}
	if(vm.tipoInstancia.comentario.id!=3){
		parseField("Comentario", parser, docDefinition, vm.data.pronunciamiento.contenido.comentario, "Sin comentario");
	}
	pdfMake.createPdf(docDefinition).download(vm.data.pronunciamiento.tipoCodigo.nombre + " " +
			vm.data.pronunciamiento.codigoPronunciamiento + ".pdf", callback);
};

var downloadFile = function(siteName, vm) {
	vm.sentencia = vm.data.pronunciamiento.contenido["sentencia" + siteName];
	$('#download-pdf-modal').modal("show");
};

var isValidUrl = function(url) {
	var regex = /(http|https):\/\/(\w+:{0,1}\w*)?(\S+)(:[0-9]+)?(\/|\/([\w#!:.?+=&%!\-\/]))?/;
	return regex.test(url);
};

var goToPronunciamiento = function ($state, vm) { 
	$state.go('Pronunciamiento', {id_pronunciamiento : vm.params.id_pronunciamiento}); 
};

function processPronunciamiento(pronunciamiento, siteName, vm, ServiceHTTP) {
	
	vm.host = ServiceHTTP.getHost();
	vm.data.pronunciamiento = pronunciamiento.data.data;
	vm.tipoInstancia = pronunciamiento.data.data.tipoPronunciamiento.tipoInstancia;
	vm.nombreTipoInstancia = vm.tipoInstancia.nombre;
	
	if (vm.data.pronunciamiento.contenido["sentencia" + siteName]) {
		vm.sentencia = vm.data.pronunciamiento.contenido["sentencia" + siteName];
		vm.texto = true;
	} else if (vm.isValidUrl(vm.data.pronunciamiento.urlDocumento)) {
		vm.downloadUrl = vm.data.pronunciamiento.urlDocumento;
		vm.texto = false;
	} else if(vm.tipoInstancia.pronunciamiento.id!=3){
		vm.downloadUrl =  vm.host + "acjui/services/data/intranetService/pronunciamiento/documento?id_file=" + pronunciamiento.data.data.urlDocumento;
		vm.texto = false;
	}else{
		vm.texto = false;
	}
	
	var normativaToArticulos = [];
	var nombreToNormativa = [];
	var nArticulos = vm.data.pronunciamiento.pronunciamientosArticulos.length;
	for (i = 0; i < nArticulos; ++i) {
		var pronunciamientoArticulo = vm.data.pronunciamiento.pronunciamientosArticulos[i];
		var articulo = pronunciamientoArticulo.articulo;
		var nota = pronunciamientoArticulo.nota;
		var normativa = articulo.tituloBO.cuerpoNormativo;
		var key = normativa.nombre;
		var value = {
			articulo: articulo,
		}
		if (nota) {
			value.nota = nota;
		}
		if (key in normativaToArticulos) {
			normativaToArticulos[key].push(value);
		} else {
			normativaToArticulos[key] = [value];
			nombreToNormativa[key] = normativa;
		}
	}
	vm.normativas = [];
	Object.keys(nombreToNormativa).sort().forEach(function(key) {
	    vm.normativas.push({
	    	normativa: nombreToNormativa[key],
	    	articulos: normativaToArticulos[key].sort(function(a, b) {
	    		if (a.articulo.numero < b.articulo.numero) return -1;
	    		if (a.articulo.numero > b.articulo.numero) return 1;
	    		if (a.nota < b.nota) return -1;
	    		return 1;
	    	}),
	    });
	});
}

function plainText(html) {
	var newLineMarker = 'HEREGOESANEWLINE';
	var spaceMarker = 'HEREGOESASPACE';
	// this makes sure to add a newline for every html BR.
	html = html.replace(/<br *\/>/g, newLineMarker);
	// this makes sure to add a newline for every html paragraph
	html = html.replace(/\/p>/g, '/p>' + newLineMarker);
	// this inserts a space between pairs of pdf copied text lines
	html = html.replace(/> *</g, '>' + spaceMarker + '<');
	
	var tmp = document.createElement("DIV");
	tmp.innerHTML = html;
	// newline markers inside tags get eliminated
	// example: <html HEREGOESANEWLINE>...
	var result = tmp.textContent || tmp.innerText || "";
	
	var newLineRegex = new RegExp('(' + newLineMarker + ')+', 'g');
	// find consecutive newline markers and replace them for a single BR
	result = result.replace(newLineRegex, '<br/>');
	var spaceRegex = new RegExp('(' + spaceMarker + ')+', 'g');
	// find consecutive space markers and replace them for a single space
	result = result.replace(spaceRegex, ' ');
	return result;
}
