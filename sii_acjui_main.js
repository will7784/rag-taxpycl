function Articulo(ServiceHTTP, $stateParams, $state, $log, $scope, $filter, $window){
	
	var vm = this;
	vm.data = {};
	vm.params = $stateParams;
	autocompleteCodigo(ServiceHTTP, $state);
	
	vm.go = function () {
		/* Tipo cuerpo 1 = Codigo Tributario, Tipo pronunciamiento 1 = Corte Suprema*/ 
		$state.go('Articulo', {tipo_instancia: vm.params.tipo_instancia,
								  tipo_cuerpo: vm.params.tipo_cuerpo, 
								  tipo_pronunciamiento: vm.params.tipo_pronunciamiento,
								  articulo:vm.params.articulo
										 });
	};

	vm.host = ServiceHTTP.getHost();
	
	vm.totalTextSingular = function() {
		return (($scope.filteredItems && $scope.filteredItems.length==1) || vm.totalItems == 1);
	};

	vm.init = function(arr, columnNames) {
		$scope.pageSizes = [ 5, 10, 25, 50 ];
		$scope.reverse = false;
		$scope.filteredItems = [];
		$scope.groupedItems = [];
		$scope.itemsPerPage = parseInt($window.localStorage.getItem('itemsPerPageExploradorArticulo')) || $scope.pageSizes[2];
		$scope.pagedItems = [];
		$scope.currentPage = 0;
		$scope.items = arr;
		$scope.columnNames = columnNames;
		$scope.sortingColumn = "codigo";

		var searchMatch = function(values, query) {
			
			if (!query) {
				return true;
			}
			
			for (var i = 0; i < values.length; i++) {
				if (values[i] && values[i].toLowerCase().indexOf(query.toLowerCase()) !== -1) {
					return true;
				}
			}
			return false;
		};
		
		var getObjectProperty = 	function(o, s) {
			s = s.replace(/\[(\w+)\]/g, '.$1'); // convert indexes to properties
			s = s.replace(/^\./, '');           // strip a leading dot
			var a = s.split('.');
			for (var i = 0, n = a.length; i < n; ++i) {
				var k = a[i];
				if (k in o) {
					o = o[k];
				} else {
					return;
				}
			}
			return o;
		};

		// init the filtered items
		$scope.search = function() {
			$scope.filteredItems = $filter('filter')(
					$scope.items,
					function(item) {
						var columnValues = [];
						for (var i = 0; i < $scope.columnNames.length; i++) {
							columnValues.push(getObjectProperty(item, $scope.columnNames[i]));
						}
						var codeColumn = getObjectProperty(item, "tipoCodigo") + " " + getObjectProperty(item, "codigo");
						columnValues.push(codeColumn);
						return searchMatch(columnValues, $scope.query);
					});
			// take care of the sorting order
			if ($scope.sortingColumn !== '') {
				$scope.filteredItems = $filter('orderBy')($scope.filteredItems, $scope.sortingColumn, $scope.reverse);
			}
			$scope.currentPage = 0;
			// now group by pages
			$scope.groupToPages();
		};

		// show items per page
		$scope.perPage = function() {
			$scope.currentPage = 0;
			$scope.groupToPages();
		};

		// calculate page in place
		$scope.groupToPages = function() {
			$scope.pagedItems = [];
			for (var i = 0; i < $scope.filteredItems.length; i++) {
				if (i % $scope.itemsPerPage === 0) {
					$scope.pagedItems[Math.floor(i / $scope.itemsPerPage)] = [ $scope.filteredItems[i] ];
				} else {
					$scope.pagedItems[Math.floor(i / $scope.itemsPerPage)].push($scope.filteredItems[i]);
				}
			}
		};

		$scope.deleteItem = function(idx) {
			var itemToDelete = $scope.pagedItems[$scope.currentPage][idx];
			var idxInItems = $scope.items.indexOf(itemToDelete);
			$scope.items.splice(idxInItems, 1);
			$scope.search();

			return false;
		};

		$scope.range = function(start, end) {
			var ret = [];
			if (!end) {
				end = start;
				start = 0;
			}
			for (var i = start; i < end; i++) {
				ret.push(i);
			}
			return ret;
		};

		$scope.prevPage = function() {
			if ($scope.currentPage > 0) {
				$scope.currentPage--;
			}
		};

		$scope.nextPage = function() {
			if ($scope.currentPage < $scope.pagedItems.length - 1) {
				$scope.currentPage++;
			}
		};

		$scope.firstPage = function() {
			$scope.currentPage = 0;
		};

		$scope.lastPage = function() {
			$scope.currentPage = $scope.pagedItems.length - 1;
		};

		$scope.setPage = function() {
			$scope.currentPage = this.n;
		};

		// change sorting order
		$scope.sort_by = function(newSortingColumn) {
			
			$scope.currentPage = 0;
			if ($scope.sortingColumn === newSortingColumn) {
				$scope.reverse = !$scope.reverse;
			}

			$scope.sortingColumn = newSortingColumn;
			$scope.sortingArray = [ newSortingColumn ];
			if (newSortingColumn === "codigo") {
				$scope.sortingArray.unshift('tipoCodigo');
			}
			$scope.filteredItems = $filter('orderBy')($scope.filteredItems, $scope.sortingArray, $scope.reverse);
			$scope.groupToPages();
		};

		$scope.search();
	};

	function mapValuesToArray(map) {
		var array = [];
		for (var key in map) {
			array.push(map[key]);
		}
		return array;
	}
	
	/* Listar tipo instancia */
	vm.data.resultados = [];
	ServiceHTTP.getTipoInstancia(vm.params.tipo_instancia, "Cargando...").then(function(data) {
		vm.tipoInstancia = data.data.data;
		return ServiceHTTP.getGrupoInstancia(vm.params.grupo_instancia);
	}).then(function(data) {
		vm.grupoInstancia = data.data.data;
		return ServiceHTTP.getCuerpoNormativo(vm.params.cuerpo_normativo);
	}).then(function(data) {
		vm.cuerpoNormativo = data.data.data;
		return ServiceHTTP.getArticulo(vm.params.articulo);
	}).then(function(data) {
		vm.articulo = data.data.data;
		return ServiceHTTP.getResultadosExplorador(vm.articulo, vm.grupoInstancia);
	}).then(function(data) {
		vm.data.resultados = data.data.data;
		vm.totalItems = vm.data.resultados.length;
		vm.init(mapValuesToArray(vm.data.resultados), ["fecha", "instancia", "resumenInternet", "ruc"]);
		ServiceHTTP.cerrarMensaje();
	}).catch(function(e) {
		console.log(e);
		ServiceHTTP.cerrarMensaje();
	});
	
	$scope.$on('$destroy', function() {
		$window.localStorage.setItem('itemsPerPageExploradorArticulo', $scope.itemsPerPage);
	});
}

angular.module('comun').controller('Articulo',Articulo);
Articulo.$inject = ['ServiceHTTP', '$stateParams', '$state', '$log', '$scope', '$filter', '$window'];

function Buscador(ServiceHTTP, $stateParams, $state, $window, $location, $log, $scope, $filter, $timeout) {
	
	
	var vm = this;
	vm.data = {};
	vm.tipoBuscador = $stateParams.tipo;
	vm.params = $location.search();
	vm.searchForm = {};
	vm.host = ServiceHTTP.getHost();
	vm.selectedArticulos = [];
	vm.data.resultados = [];
	vm.fechaDesdeNoEspecificada = true;
	vm.fechaHastaHoy = true;
	
	autocompleteCodigo(ServiceHTTP, $state);
	
	vm.go = function() {
		$state.go('Buscador');
	};
	
	var initReadonlySelects = function() {
		$('select').unbind('mousedown keydown');
		$('select[readonly]').on('mousedown keydown', function() { 
			return false;
		});	
	};
	
	var initTooltips = function() {
		$('[data-toggle="tooltip"]').tooltip('enable');
		$('[data-toggle="tooltip"].tooltip-disabled').tooltip('disable');
		initReadonlySelects();
	};
	
	var changeTooltipStatus = function(action, elementIdList) {
		
		for (var i = 0; i < elementIdList.length; i++) {
			var $element = $('#' + elementIdList[i]);
			switch (action) {
				case 'enable':
					$element.removeClass('tooltip-disabled');
					break;
				case 'disable':
					$element.addClass('tooltip-disabled');
					break;
				default:
					$element.toggleClass('tooltip-disabled');
			}
		}
		$timeout(initTooltips, 0);
	};
	
	/*---------------------------------------------- Paginado ----------------------------------------------*/
	
	vm.init = function(arr, columnNames) {
		$scope.pageSizes = [ 5, 10, 25, 50 ];
		$scope.reverse = true;
		$scope.filteredItems = [];
		$scope.groupedItems = [];
		$scope.itemsPerPage = $scope.pageSizes[2];
		$scope.pagedItems = [];
		$scope.currentPage = 0;
		$scope.items = arr;
		$scope.columnNames = columnNames;
		$scope.sortingColumn = "rank";

		var searchMatch = function(values, query) {
			
			if (!query) {
				return true;
			}
			
			for (var i = 0; i < values.length; i++) {
				if (values[i] && values[i].toLowerCase().indexOf(query.toLowerCase()) !== -1) {
					return true;
				}
			}
			return false;
		};
		
		var getObjectProperty = 	function(o, s) {
			s = s.replace(/\[(\w+)\]/g, '.$1'); // convert indexes to properties
			s = s.replace(/^\./, '');           // strip a leading dot
			var a = s.split('.');
			for (var i = 0, n = a.length; i < n; ++i) {
				var k = a[i];
				if (k in o) {
					o = o[k];
				} else {
					return;
				}
			}
			return o;
		};

		// init the filtered items
		$scope.search = function() {
			$scope.filteredItems = $filter('filter')(
					$scope.items,
					function(item) {
						var columnValues = [];
						for (var i = 0; i < $scope.columnNames.length; i++) {
							columnValues.push(getObjectProperty(item, $scope.columnNames[i]));
						}
						var codeColumn = getObjectProperty(item, "tipoCodigo") + " " + getObjectProperty(item, "codigo");
						columnValues.push(codeColumn);
						return searchMatch(columnValues, $scope.query);
					});
			// take care of the sorting order
			if ($scope.sortingColumn !== '') {
				$scope.filteredItems = $filter('orderBy')($scope.filteredItems, $scope.sortingColumn, $scope.reverse);
			}
			$scope.currentPage = 0;
			// now group by pages
			$scope.groupToPages();
		};

		// show items per page
		$scope.perPage = function() {
			$scope.currentPage = 0;
			$scope.groupToPages();
		};

		// calculate page in place
		$scope.groupToPages = function() {
			$scope.pagedItems = [];
			for (var i = 0; i < $scope.filteredItems.length; i++) {
				if (i % $scope.itemsPerPage === 0) {
					$scope.pagedItems[Math.floor(i / $scope.itemsPerPage)] = [ $scope.filteredItems[i] ];
				} else {
					$scope.pagedItems[Math.floor(i / $scope.itemsPerPage)].push($scope.filteredItems[i]);
				}
			}
		};

		$scope.deleteItem = function(idx) {
			var itemToDelete = $scope.pagedItems[$scope.currentPage][idx];
			var idxInItems = $scope.items.indexOf(itemToDelete);
			$scope.items.splice(idxInItems, 1);
			$scope.search();

			return false;
		};

		$scope.range = function(start, end) {
			var ret = [];
			if (!end) {
				end = start;
				start = 0;
			}
			for (var i = start; i < end; i++) {
				ret.push(i);
			}
			return ret;
		};

		updateTableState = function(){
			vm.tableState = {};
			vm.tableState.sortingColumn = $scope.sortingColumn;
			vm.tableState.reverse = $scope.reverse;
			vm.tableState.page = $scope.currentPage;
			sessionStorage.removeItem("intranet-search-results-tableState");
			sessionStorage.setItem("intranet-search-results-tableState", JSON.stringify(vm.tableState));
		};

		$scope.prevPage = function() {
			if ($scope.currentPage > 0) {
				$scope.currentPage--;
				updateTableState();
			}
		};

		$scope.nextPage = function() {
			if ($scope.currentPage < $scope.pagedItems.length - 1) {
				$scope.currentPage++;
				updateTableState();
			}
		};

		$scope.firstPage = function() {
			$scope.currentPage = 0;
			updateTableState();
		};

		$scope.lastPage = function() {
			$scope.currentPage = $scope.pagedItems.length - 1;
			updateTableState();
		};

		$scope.setPage = function() {
			$scope.currentPage = this.n;
			updateTableState();
		};
		// change sorting order
		$scope.sort_by = function(newSortingColumn) {
			
			$scope.currentPage = 0;
			if ($scope.sortingColumn === newSortingColumn) {
				$scope.reverse = !$scope.reverse;
			}

			$scope.sortingColumn = newSortingColumn;
			$scope.sortingArray = [ newSortingColumn, "rank", "fecha"];
			if (newSortingColumn === "codigo") {
				$scope.sortingArray.unshift('tipoCodigo');
			}
			$scope.filteredItems = $filter('orderBy')($scope.filteredItems, $scope.sortingArray, $scope.reverse);
			$scope.groupToPages();
		};

		$scope.search();
	};
	
	/*------------------------------------------------------------------------------------------------------*/
	
	function mapValuesToArray(map) {
		var array = [];
		for (var key in map) {
			array.push(map[key]);
		}
		return array;
	}
	
	function byNombre(a, b) {
		if (a.nombre < b.nombre) {
			return -1;
		} else if (a.nombre > b.nombre) {
			return 1;
		}
		return 0;
	}
	
	function prepareOptions(options, title) {
		options.sort(byNombre);
		options.unshift({nombre: title, id: null});
		return options;
	}
	
	function setMinDate() {
		$('#fecha-hasta').datepicker('option', 'minDate', vm.fechaDesde);
	}
	
	function setMaxDate() {
		$('#fecha-desde').datepicker('option', 'maxDate', vm.fechaHasta);
	}
	
	$("#fecha-desde").datepicker({
		onSelect: function(dateText) {
			vm.fechaDesde = dateText;
			setMinDate();
		}
	});
	$("#fecha-hasta").datepicker({
		onSelect: function(dateText) {
			vm.fechaHasta = dateText;
			setMaxDate();
		}
	});
	
	function selectArticuloBuscado() {
		vm.selectedArticulo = vm.articulos.find(function(articulo) {
			return articulo.id == vm.params.articulo;
		});
		vm.addArticulo();
		vm.findPronunciamientos();
	}
	
	function initFormFinalStep() {
		
		var results = sessionStorage.getItem("internet-search-results");
		if (results) {
			vm.data.resultados = JSON.parse(results);
			if (vm.data.resultados.length > 0) {
				vm.init(vm.data.resultados,["fecha", "instancia", "resumenInternet", "ruc"]);
				vm.noResults = false;
				vm.searchFormJson = JSON.stringify(vm.searchForm);
				var tableStateJson = sessionStorage.getItem("intranet-search-results-tableState");
				if(tableStateJson){
					var tableState = JSON.parse(tableStateJson);
					$scope.sortingColumn = "";
					$scope.reverse = tableState.reverse;
					$scope.sort_by(tableState.sortingColumn);
					$scope.currentPage = tableState.page;
				}
			} else {
				vm.noResults = true;
			}
		}
	}
	
	function initFormStep3() {
		var articulos = JSON.parse(sessionStorage.getItem("internet-search-results-articulos"));
		for (var i = 0; i < articulos.length; i++) {
			vm.selectedArticulo = articulos[i];
			vm.addArticulo();
		}
		initFormFinalStep();
	}
	
	function initFormStep2() {
		
		if (vm.searchForm.instanciaId) {
			vm.selectedInstancia = vm.instancias.find(function(instancia) {
				return instancia.id == vm.searchForm.instanciaId;
			});
		}
		
		if (vm.searchForm.cuerpoNormativoId) {
			
			vm.selectedCuerpoNormativo = vm.cuerposNormativos.find(function(tipoInstancia) {
				return tipoInstancia.id == vm.searchForm.cuerpoNormativoId;
			});
			
			vm.updateArticuloOptions(initFormStep3);
			
		} else {
			initFormStep3();
		}
	}
	
	function initFormStep1() {
		
		vm.searchText = vm.searchForm.text;
		vm.ruc = vm.searchForm.ruc;
		
		if (vm.searchForm.fechaDesde) {
			vm.fechaDesde = $.datepicker.formatDate(DATE_FORMAT, vm.searchForm.fechaDesde);
			vm.fechaDesdeNoEspecificada = false;
		}
		
		if (vm.searchForm.fechaHasta) {
			vm.fechaHasta = $.datepicker.formatDate(DATE_FORMAT, vm.searchForm.fechaHasta);
			vm.fechaHastaHoy = false;
		}
		
		vm.codigo = vm.searchForm.codigo;
		vm.partes = vm.searchForm.partes;
		
		if (vm.searchForm.tipoCodigoId) {
			vm.selectedTipoCodigo = vm.codigos.find(function(tipoCodigo) {
				return tipoCodigo.id == vm.searchForm.tipoCodigoId;
			});
		}
		
		if (vm.searchForm.tipoPronunciamientoId) {
			vm.selectedTipoPronunciamiento = vm.tiposPronunciamiento.find(function(tipoPronunciamiento) {
				return tipoPronunciamiento.id == vm.searchForm.tipoPronunciamientoId;
			});
		}
		
		if (vm.searchForm.grupoInstanciaId) {
			
			vm.selectedGrupoInstancia = vm.gruposInstancia.find(function(grupoInstancia) {
				return grupoInstancia.id == vm.searchForm.grupoInstanciaId;
			});
			
			vm.updateInstanciaOptions(initFormStep2);
			
		} else {
			initFormStep2();
		}
	}
	
	vm.instancias = [{nombre: "Todos", id: null}];
	vm.selectedInstancia = vm.instancias[0];
	vm.articulos = [{nombre: "Todos", id: null}];
	vm.selectedArticulo = vm.articulos[0];

	ServiceHTTP.listTiposInstancia("Cargando").then(function(tiposInstancias) {
		vm.selectedTipoInstancia = tiposInstancias.data.data[0];
		return ServiceHTTP.listCuerposNormativos("Cargando");
	}).then(function(cuerposNormativos) {
		vm.cuerposNormativos = cuerposNormativos.data.data;
		vm.selectedCuerpoNormativo = prepareOptions(vm.cuerposNormativos, "Todas")[0];
		return ServiceHTTP.findGruposDeInstancia(vm.selectedTipoInstancia.id);
	}).then(function(gruposInstancias) {
		vm.gruposInstancia = gruposInstancias.data.data;
		vm.selectedGrupoInstancia = prepareOptions(vm.gruposInstancia, "Todos")[0];
		return ServiceHTTP.findTiposDePronunciamiento(vm.selectedTipoInstancia.id);
	}).then(function(tiposPronunciamientos) {
		vm.tiposPronunciamiento = tiposPronunciamientos.data.data;
		vm.selectedTipoPronunciamiento = prepareOptions(vm.tiposPronunciamiento, "Todos")[0];
		return ServiceHTTP.findTipoCodigo();
	}).then(function(codigos) {
		vm.codigos = codigos.data.data;
		vm.selectedTipoCodigo = prepareOptions(vm.codigos, "Todos")[0];
		
		$timeout(initTooltips, 0);
		
		if($stateParams.codigo){
			vm.codigo = $stateParams.codigo;
			vm.exacto = $stateParams.exacto;
			if (history.state) {
				vm.searchForm = history.state;
				initFormStep1();
			}else{
				vm.findPronunciamientos();
			}
		}else if (vm.params.codigo) {
			vm.codigo = vm.params.codigo;
			vm.exacto = vm.params.exacto;
			if (history.state) {
				vm.searchForm = history.state;
				initFormStep1();
			}else{
				vm.findPronunciamientos();
			}
		}else if (vm.params.normativa) {
			vm.selectedCuerpoNormativo = vm.cuerposNormativos.find(function(cuerpoNormativo) {
				return cuerpoNormativo.id == vm.params.normativa;
			});
			if (vm.params.articulo) {
				vm.updateArticuloOptions(selectArticuloBuscado);
			} else {
				vm.updateArticuloOptions(vm.findPronunciamientos);
			}
		}else if (history.state) {
			vm.searchForm = history.state;
			initFormStep1();
		}
		
		ServiceHTTP.cerrarMensaje();
	}).catch(function(e) {
		console.log(e);
		ServiceHTTP.cerrarMensaje();
	});
	
	vm.updateInstanciaOptions = function(callbackFn) {
		
		if (vm.selectedGrupoInstancia.id) {
			ServiceHTTP.findInstancias(vm.selectedGrupoInstancia.id).then(function(instancias) {
				vm.instancias = instancias.data.data;
				vm.selectedInstancia = prepareOptions(vm.instancias, "Todas")[0];
				
				changeTooltipStatus('disable', ['instancia']);
				
				if (callbackFn) {
					callbackFn();
				}
			}, function(e) {
				console.log(e);
			});
		} else {
			vm.instancias = [{nombre: "Todos", id: null}];
			vm.selectedInstancia = vm.instancias[0];
			
			changeTooltipStatus('enable', ['instancia']);
			
			if (callbackFn) {
				callbackFn();
			}
		}
	};
	
	vm.updateArticuloOptions = function(callbackFn) {
		
		if (vm.selectedCuerpoNormativo.id) {
			ServiceHTTP.findArticulos(vm.selectedCuerpoNormativo.id).then(function(articulos) {
				vm.articulos = articulos.data.data;
				vm.selectedArticulo =  (vm.articulos)? vm.articulos[0]:null;
				
				changeTooltipStatus('disable', ['articulo']);
				
				if (callbackFn) {
					callbackFn();
				}
			}, function(e) {
				console.log(e);
			});
		} else {
			vm.removeAllArticulos();
			vm.articulos = [{nombre: "Todos", id: null}];
			vm.selectedArticulo = vm.articulos[0];
			
			changeTooltipStatus('enable', ['articulo']);
			
			if (callbackFn) {
				callbackFn();
			}
		}
	};
	
	vm.addArticulo = function() {
		var articulo = {
			id: vm.selectedArticulo.id,
			nombre: vm.selectedArticulo.nombre,
			normativa: vm.selectedCuerpoNormativo.nombre
		};
		vm.selectedArticulos.push(articulo);
		vm.articulos = vm.articulos.filter(function(element) {
			return element.id && element.id !== vm.selectedArticulo.id;
		});
		vm.selectedArticulo = null;
	};
	
	vm.removeArticulo = function(item) {
		vm.selectedArticulos = vm.selectedArticulos.filter(function(element) {
			return element.id != item.id;
		});
		vm.articulos.push(item);
		if (vm.selectedArticulos.length === 0) {
			vm.selectedArticulo = prepareOptions(vm.articulos, "Todos")[0];
		}
	};
	
	vm.removeAllArticulos = function() {
		for (var i = 0; i < vm.selectedArticulos.length; i++) {
		    vm.articulos.push(vm.selectedArticulos[i]);
		}
		vm.selectedArticulos = [];
		vm.selectedArticulo = prepareOptions(vm.articulos, "Todos")[0];
	};
	
	vm.toggleFechaDesde = function() {
		if (!vm.fechaDesdeNoEspecificada) {
			vm.fechaDesde = vm.fechaDesdePrevia || null;
		} else {
			vm.fechaDesdePrevia = vm.fechaDesde;
			vm.fechaDesde = null;
		}
		setMinDate();
	};
	
	vm.toggleFechaHasta = function() {
		if (!vm.fechaHastaHoy) {
			if(vm.fechaHastaPrevia){
				vm.fechaHasta = vm.fechaHastaPrevia;
			}
		} else {
			vm.fechaHastaPrevia = vm.fechaHasta;
			vm.fechaHasta = null;
		}
		setMaxDate();
	};

	vm.findPronunciamientos = function() {
			
		var articulosIds = vm.selectedArticulos.map(function(articulo) {
			return articulo.id;
		});
		
		vm.searchForm = {
			text: vm.searchText ? vm.searchText : null,
			tipoInstanciaId: vm.selectedTipoInstancia ? vm.selectedTipoInstancia.id : null,
			grupoInstanciaId: vm.selectedGrupoInstancia ? vm.selectedGrupoInstancia.id : null,
			tipoCodigoId: vm.selectedTipoCodigo ? vm.selectedTipoCodigo.id : null,
			codigo: vm.codigo? vm.codigo : null,
			ruc : vm.ruc? vm.ruc : null,
			instanciaId: vm.selectedInstancia ? vm.selectedInstancia.id : null,
			tipoPronunciamientoId: vm.selectedTipoPronunciamiento ? vm.selectedTipoPronunciamiento.id : null,
			cuerpoNormativoId: null,
			articulosIds: articulosIds,
			reemplazos: [],
			fechaDesde: vm.fechaDesde ? $.datepicker.parseDate(DATE_FORMAT, vm.fechaDesde) : null,
			fechaHasta: vm.fechaHasta ? $.datepicker.parseDate(DATE_FORMAT, vm.fechaHasta) : null
		};
		
		ServiceHTTP.findPronunciamientos(vm.searchForm, "Buscando pronunciamientos").then(function(data) {
			vm.data.resultados = mapValuesToArray(data.data.data);
			if (vm.data.resultados.length > 0) {
				vm.init(vm.data.resultados,["fecha", "instancia", "resumenInternet", "ruc"]);
				vm.noResults = false;
				vm.searchFormJson = JSON.stringify(vm.searchForm);
			} else {
				vm.noResults = true;
			}
			
			history.replaceState(vm.searchForm, "internet-search-results");
			vm.tableState = {};
			vm.tableState.sortingColumn = $scope.sortingColumn;
			vm.tableState.reverse = $scope.reverse;
			vm.tableState.page = 0;
			sessionStorage.setItem("intranet-search-results-articulos", JSON.stringify(vm.selectedArticulos));
			sessionStorage.setItem("intranet-search-results", JSON.stringify(vm.data.resultados));
			sessionStorage.setItem("intranet-search-results-tableState", JSON.stringify(vm.tableState));
			
			ServiceHTTP.cerrarMensaje();
		}, function(e) {
			vm.noResults = true;
			console.log(e);
			ServiceHTTP.cerrarMensaje();
		});
	};
	
	vm.totalTextSingular = function() {
		return (($scope.filteredItems && $scope.filteredItems.length==1) || vm.data.resultados.length == 1);
	};
	
	vm.clearForm = function() {
		vm.searchText = null;
		vm.selectedGrupoInstancia = prepareOptions(vm.gruposInstancia, "Todos")[0];
		vm.selectedTipoPronunciamiento = prepareOptions(vm.tiposPronunciamiento, "Todos")[0];
		vm.selectedTipoCodigo.id = null;
		vm.codigo = null;
		vm.ruc = null;
		vm.selectedInstancia.id = null;
		vm.selectedCuerpoNormativo.id = null;
		vm.selectedArticulo = null;
		vm.selectedArticulos = [];
		vm.fechaDesde = null;
		vm.fechaHasta = null;
		vm.fechaDesdeNoEspecificada = true;
		vm.fechaHastaHoy = true;
		vm.updateInstanciaOptions();
		vm.updateArticuloOptions();
		vm.data.resultados = [];
		vm.noResults = false;
		
		history.replaceState(null, "internet-search-results");
		sessionStorage.removeItem("intranet-search-results-articulos");
		sessionStorage.removeItem("intranet-search-results");
		sessionStorage.removeItem("intranet-search-results-tableState");
	};
	
}

angular.module('comun').controller('Buscador', Buscador);
Buscador.$inject = [ 'ServiceHTTP', '$stateParams', '$state', '$window', '$location', '$log', '$scope', '$filter', '$timeout' ];

function CuerpoNormativo(ServiceHTTP, $stateParams, $state, $log, $scope, $filter, $window) {
	
	var vm = this;
	vm.data = {};
	vm.params = $stateParams;	
	autocompleteCodigo(ServiceHTTP, $state);
	
	vm.go = function () {
		$state.go('CuerpoNormativo', {
			tipo_instancia: vm.params.tipo_instancia,
			tipo_cuerpo: vm.params.cuerpo_normativo, 
			tipo_pronunciamiento: vm.params.grupo_instancia
		});
	};
	
	vm.init = function(arr, columnNames) {
		$scope.pageSizes = [ 5, 10, 25, 50 ];
		$scope.reverse = false;
		$scope.filteredItems = [];
		$scope.groupedItems = [];
		$scope.itemsPerPage = parseInt($window.localStorage.getItem('itemsPerPageExploradorCuerpoNormativo')) || $scope.pageSizes[0];
		$scope.pagedItems = [];
		$scope.currentPage = 0;
		$scope.items = arr;
		$scope.columnNames = columnNames;
		$scope.sortingColumn = "";

		var searchMatch = function(values, query) {
			
			if (!query) {
				return true;
			}
			
			for (var i = 0; i < values.length; i++) {
				if (values[i] && values[i].toLowerCase().indexOf(query.toLowerCase()) !== -1) {
					return true;
				}
			}
			return false;
		};
		
		var getObjectProperty = 	function(o, s) {
		    s = s.replace(/\[(\w+)\]/g, '.$1'); // convert indexes to properties
		    s = s.replace(/^\./, '');           // strip a leading dot
		    var a = s.split('.');
		    for (var i = 0, n = a.length; i < n; ++i) {
		        var k = a[i];
		        if (k in o) {
		            o = o[k];
		        } else {
		            return;
		        }
		    }
		    return o;
		};

		// init the filtered items
		$scope.search = function() {
			$scope.filteredItems = $filter('filter')(
					$scope.items,
					function(item) {
						var columnValues = [];
						for (var i = 0; i < $scope.columnNames.length; i++) {
							columnValues.push(getObjectProperty(item, $scope.columnNames[i]));
						}
						return searchMatch(columnValues, $scope.query);
					});
			// take care of the sorting order
			if ($scope.sortingColumn !== '') {
				if($scope.sortingColumn === "nombreArticulo"){
					$scope.sortingArray = ['numeroArticulo','adverbioArticulo','id_articulo'];
				}else{
					$scope.sortingArray = [$scope.sortingColumn];
				}
				$scope.filteredItems = $filter('orderBy')($scope.filteredItems, $scope.sortingArray, $scope.reverse);
			}
			$scope.currentPage = 0;
			// now group by pages
			$scope.groupToPages();
		};

		// show items per page
		$scope.perPage = function() {
			$scope.currentPage = 0;
			$scope.groupToPages();
		};

		// calculate page in place
		$scope.groupToPages = function() {
			$scope.pagedItems = [];
			for (var i = 0; i < $scope.filteredItems.length; i++) {
				if (i % $scope.itemsPerPage === 0) {
					$scope.pagedItems[Math.floor(i / $scope.itemsPerPage)] = [ $scope.filteredItems[i] ];
				} else {
					$scope.pagedItems[Math.floor(i / $scope.itemsPerPage)].push($scope.filteredItems[i]);
				}
			}
		};

		$scope.deleteItem = function(idx) {
			var itemToDelete = $scope.pagedItems[$scope.currentPage][idx];
			var idxInItems = $scope.items.indexOf(itemToDelete);
			$scope.items.splice(idxInItems, 1);
			$scope.search();

			return false;
		};

		$scope.range = function(start, end) {
			var ret = [];
			if (!end) {
				end = start;
				start = 0;
			}
			for (var i = start; i < end; i++) {
				ret.push(i);
			}
			return ret;
		};

		$scope.prevPage = function() {
			if ($scope.currentPage > 0) {
				$scope.currentPage--;
			}
		};

		$scope.nextPage = function() {
			if ($scope.currentPage < $scope.pagedItems.length - 1) {
				$scope.currentPage++;
			}
		};

		$scope.firstPage = function() {
			$scope.currentPage = 0;
		};

		$scope.lastPage = function() {
			$scope.currentPage = $scope.pagedItems.length - 1;
		};

		$scope.setPage = function() {
			$scope.currentPage = this.n;
		};

		// change sorting order
		$scope.sort_by = function(newSortingColumn) {
			
			$scope.currentPage = 0;
			if ($scope.sortingColumn === newSortingColumn) {
				$scope.reverse = !$scope.reverse;
			}

			$scope.sortingColumn = newSortingColumn;
			$scope.sortingArray = [ newSortingColumn ];
			if (newSortingColumn === "total") {
				$scope.filteredItems = $scope.filteredItems.map(function(item, index) {
					item["total"] = item.instancias[0].total;
					return item;
				});
				$scope.sortingArray = ["total"];
			}else if(newSortingColumn === "nombre"){
				$scope.sortingArray = ['numeroArticulo','adverbioArticulo','id_articulo'];
			}
			$scope.filteredItems = $filter('orderBy')($scope.filteredItems, $scope.sortingArray, $scope.reverse);
			$scope.groupToPages();
		};

		$scope.search();
	};
	
	vm.data.resumenNivel2 = [];
	ServiceHTTP.getTipoInstancia(vm.params.tipo_instancia, "Cargando...").then(function(data) {
		vm.tipoInstancia = data.data.data;
		return ServiceHTTP.getGrupoInstancia(vm.params.grupo_instancia);
	}).then(function(data) {
		vm.grupoInstancia = data.data.data;
		return ServiceHTTP.getCuerpoNormativo(vm.params.cuerpo_normativo);
	}).then(function(data) {
		vm.cuerpoNormativo = data.data.data;
		return ServiceHTTP.getResumenNivel2(vm.grupoInstancia, vm.cuerpoNormativo);
	}).then(function(data) {
		vm.data.resumenNivel2 = data.data.data;
		vm.init(vm.data.resumenNivel2.body, ["nombre"]);
		ServiceHTTP.cerrarMensaje();
	}).catch(function(e) {
		console.log(e);
		ServiceHTTP.cerrarMensaje();
	});

	$scope.$on('$destroy', function() {
		$window.localStorage.setItem('itemsPerPageExploradorCuerpoNormativo', $scope.itemsPerPage);
	});
}

angular.module('comun').controller('CuerpoNormativo', CuerpoNormativo);
CuerpoNormativo.$inject = ['ServiceHTTP', '$stateParams', '$state', '$log', '$scope', '$filter', '$window'];

function Instancia(ServiceHTTP, $stateParams, $state, $window, $log, $scope, $filter) {
	
	var vm = this;
	vm.data = {};
	vm.params = $stateParams;
	vm.go = function() {
		$state.go('Instancia', {tipo_instancia : vm.params.tipo_instancia});
	};
	
	autocompleteCodigo(ServiceHTTP, $state);

	vm.init = function(arr, columnNames) {
		$scope.pageSizes = [ 5, 10, 25, 50 ];
		$scope.reverse = false;
		$scope.filteredItems = [];
		$scope.groupedItems = [];
		$scope.itemsPerPage = parseInt($window.localStorage.getItem('itemsPerPageExploradorInstancia')) || $scope.pageSizes[2];
		$scope.pagedItems = [];
		$scope.currentPage = 0;
		$scope.items = arr;
		$scope.columnNames = columnNames;
		$scope.sortingColumn = columnNames[0];

		var searchMatch = function(values, query) {
			
			if (!query) {
				return true;
			}
			
			for (var i = 0; i < values.length; i++) {
				if (values[i] && values[i].toLowerCase().indexOf(query.toLowerCase()) !== -1) {
					return true;
				}
			}
			return false;
		};
		
		var getObjectProperty = 	function(o, s) {
			s = s.replace(/\[(\w+)\]/g, '.$1'); // convert indexes to properties
			s = s.replace(/^\./, '');           // strip a leading dot
			var a = s.split('.');
			for (var i = 0, n = a.length; i < n; ++i) {
				var k = a[i];
				if (k in o) {
					o = o[k];
				} else {
					return;
				}
			}
			return o;
		};

		// init the filtered items
		$scope.search = function() {
			$scope.filteredItems = $filter('filter')(
					$scope.items,
					function(item) {
						var columnValues = [];
						for (var i = 0; i < $scope.columnNames.length; i++) {
							columnValues.push(getObjectProperty(item, $scope.columnNames[i]));
						}
						return searchMatch(columnValues, $scope.query);
					});
			// take care of the sorting order
			if ($scope.sortingColumn !== '') {
				$scope.filteredItems = $filter('orderBy')($scope.filteredItems, $scope.sortingColumn, $scope.reverse);
			}
			$scope.currentPage = 0;
			// now group by pages
			$scope.groupToPages();
		};

		// show items per page
		$scope.perPage = function() {
			$scope.currentPage = 0;
			$scope.groupToPages();
		};

		// calculate page in place
		$scope.groupToPages = function() {
			$scope.pagedItems = [];
			for (var i = 0; i < $scope.filteredItems.length; i++) {
				if (i % $scope.itemsPerPage === 0) {
					$scope.pagedItems[Math.floor(i / $scope.itemsPerPage)] = [ $scope.filteredItems[i] ];
				} else {
					$scope.pagedItems[Math.floor(i / $scope.itemsPerPage)].push($scope.filteredItems[i]);
				}
			}
		};

		$scope.deleteItem = function(idx) {
			var itemToDelete = $scope.pagedItems[$scope.currentPage][idx];
			var idxInItems = $scope.items.indexOf(itemToDelete);
			$scope.items.splice(idxInItems, 1);
			$scope.search();

			return false;
		};

		$scope.range = function(start, end) {
			var ret = [];
			if (!end) {
				end = start;
				start = 0;
			}
			for (var i = start; i < end; i++) {
				ret.push(i);
			}
			return ret;
		};

		$scope.prevPage = function() {
			if ($scope.currentPage > 0) {
				$scope.currentPage--;
			}
		};

		$scope.nextPage = function() {
			if ($scope.currentPage < $scope.pagedItems.length - 1) {
				$scope.currentPage++;
			}
		};

		$scope.firstPage = function() {
			$scope.currentPage = 0;
		};

		$scope.lastPage = function() {
			$scope.currentPage = $scope.pagedItems.length - 1;
		};

		$scope.setPage = function() {
			$scope.currentPage = this.n;
		};

		// change sorting order
		$scope.sort_by = function(newSortingColumn) {
			$scope.currentPage = 0;
			if ($scope.sortingColumn === newSortingColumn) {
				$scope.reverse = !$scope.reverse;
			}
			$scope.sortingColumn = newSortingColumn;
			$scope.sortingArray = [ newSortingColumn ];
			if (typeof newSortingColumn === "number") {
				$scope.filteredItems = $scope.filteredItems.map(function(item, index) {
					item["total"] = (item.cells[newSortingColumn])? item.cells[newSortingColumn]:0;
					return item;
				});
				$scope.sortingArray = ["total","nombre"];
			}
			$scope.filteredItems = $filter('orderBy')($scope.filteredItems, $scope.sortingArray, $scope.reverse);
			
			$scope.groupToPages();
		};

		$scope.search();
	};
	
	/* Resumen nivel 1 */
	vm.data.resumenNivel1 = [];
	ServiceHTTP.getTipoInstancia(vm.params.tipo_instancia, "Cargando...").then(function(data) {
		vm.tipoInstancia = data.data.data;
		ServiceHTTP.getResumenNivel1(vm.params.tipo_instancia).then(function(data) {
			vm.data.resumenNivel1 = data.data.data;
			vm.init(vm.data.resumenNivel1.body, ["nombre"]);
			ServiceHTTP.cerrarMensaje();
		}, function(e) {
			console.log(e);
			ServiceHTTP.cerrarMensaje();
		});
	}, function(e) {
		ServiceHTTP.listTiposInstancia("Cargando...").then(function(data) {
			var tiposInstancia = data.data.data;
			if (tiposInstancia.length > 0) {
				$state.go('Instancia', {tipo_instancia : tiposInstancia[0].id});
			} else {
				$state.go('Buscador');
			}
			ServiceHTTP.cerrarMensaje();
		}, function(e) {
			console.log(e);
			ServiceHTTP.cerrarMensaje();
		});
		ServiceHTTP.cerrarMensaje();
	});
	
	$scope.$on('$destroy', function() {
		$window.localStorage.setItem('itemsPerPageExploradorInstancia', $scope.itemsPerPage);
	});
}

angular.module('comun').controller('Instancia', Instancia);
Instancia.$inject = [ 'ServiceHTTP', '$stateParams', '$state', '$window', '$log', '$scope', '$filter' ];

function Pronunciamiento(ServiceHTTP, $log, $stateParams, $scope, $state, $window, FactoryLoader, $timeout) {
	
	var vm = this;
	vm.data = {};
	vm.params = $stateParams;
	var LARGE_CONTENT_HEIGHT = 429;
	autocompleteCodigo(ServiceHTTP, $state);
	
	vm.goBack = function () {
		$window.history.back();
	};
	
	var initExpandBtns = function() {
		
		// Sticky position cross browser support
		Stickyfill.add($('.sticky-label'));
		
		$.each(['extracto', 'pronunciamiento'], function(index, value) {
			if ($('#' + value + '-content').height() >= LARGE_CONTENT_HEIGHT) {
				$('#' + value + '-expand-btn').removeClass('hidden');
			}
		});
	};
	
	vm.toogleContent = function(contentName) {
		$('#' + contentName + '-content').toggleClass('collapsed-content');
		$('#' + contentName + '-expand-btn i').toggleClass('fa-plus-square fa-minus-square');
		$('#' + contentName + '-expand-btn').toggleClass('sticky-label');
	};

	ServiceHTTP.getFullPronunciamiento(vm.params.id, "Cargando").then(function(pronunciamiento) {
		if (pronunciamiento) {
			processPronunciamiento(pronunciamiento, "Internet", vm, ServiceHTTP);
		}
		ServiceHTTP.cerrarMensaje();
		$timeout(initExpandBtns, 0);
	}, function(e) {
		$state.go('Instancia'); 
	});
	vm.isValidUrl = isValidUrl;

	vm.htmlToPdf = function() {
		FactoryLoader.activar("Cargando...");
		htmlToPdf("Internet", vm, function() {
			FactoryLoader.desactivar();
		});
	};

	vm.downloadFile = function () {
		downloadFile("Internet", vm);
	};
}

angular.module('comun').controller('Pronunciamiento', Pronunciamiento);
Pronunciamiento.$inject = [ 'ServiceHTTP', '$log', '$stateParams', '$scope', '$state', '$window', 'FactoryLoader', '$timeout' ];

function Home(ServiceHTTP, $stateParams, $state) {
	
	var vm = this;
	vm.data = {};
	vm.params = $stateParams;
//	autocompleteCodigo(ServiceHTTP, $state);
	function inArray(target, array) {
		for (var i = 0; i < array.length; i++) {
			if (array[i] === target) {
				return true;
			}
		}
		return false;
	}
	var availableRoutes = [ 'buscador', 'buscador_avanzado','home'];
	if (!(inArray(vm.params.tipo_home, availableRoutes))) {
		$state.go('Instancia', {
			tipo_instancia : ''
		});
	}
	vm.data.tiposInstancia = [];
	ServiceHTTP.getAllObject("tipo-instancia").then(function(data) {
		vm.data.tiposInstancia = data.data.data;
		ServiceHTTP.cerrarMensaje();
	}, function(e) {
		console.log(e);
		ServiceHTTP.cerrarMensaje();
	});
}

angular.module('comun').controller('Home', Home);

Home.$inject = [ 'ServiceHTTP', '$stateParams', '$state', '$log' ];
function ServiceHTTP($q, FactoryLoader, $http, $location, CONFIG, $log, $timeout, $state) {
	var token = getCookie("TOKEN") || "####";

	function createUUID() {
		// http://www.ietf.org/rfc/rfc4122.txt
		var s = [];
		var hexDigits = "0123456789abcdef";
		for (var i = 0; i < 36; i++) {
			s[i] = hexDigits.substr(Math.floor(Math.random() * 0x10), 1);
		}
		s[14] = "4"; // bits 12-15 of the time_hi_and_version field to 0010
		s[19] = hexDigits.substr((s[19] & 0x3) | 0x8, 1); // bits 6-7 of the
		// clock_seq_hi_and_reserved
		// to 01
		s[8] = s[13] = s[18] = s[23] = "-";

		var uuid = s.join("");
		return uuid;
	}

	var data = {
		metaData : {
			"namespace" : null,
			"conversationId" : token,
			"transactionId" : createUUID(),
			"page" : null
		},
		data : {}
	};
	var config = {};
	var defered = $q.defer();
	var promise = defered.promise;
	var host = $location.host();
	var port = CONFIG.port;
	var protocol = $location.protocol();
	host = protocol + "://" + host + ":" + port + "/";
	var jsonCookie = function() {
		var cookie = document.cookie;
		var output = {};
		cookie.split(/\s*;\s*/).forEach(function(pair) {
			pair = pair.split(/\s*=\s*/);
			output[pair[0]] = pair.splice(1).join('=');
		});
		return output;
	};

	function getHost() {
		return host;
	}

	function errorModa(msg, _id) {
		return "";
	}

	function controlDeErrores(http, customId, conCaptcha) {
		var _id = 'modal-http-msg-error';
		if (customId) {
			_id = 'customId';
		}
		var id = '#' + _id;
		$log.log(http);

		var defered = $q.defer();
		var promise = defered.promise;

		http.success(function(response) {
			if (!conCaptcha) {
				$('.modal').modal("hide");
				$('body').removeClass('modal-open');
			}

			if (response.metaData && response.metaData.errors) {
				$("html").append(errorModa(response.metaData.errors[0].descripcion, _id));
				$(id).modal("show");
				$(id).on("hidden.bs.modal", function() {
					$('.modal.fade.in').removeClass('modal fade').css('display', 'none');
					$('body').removeClass('modal-open');
					$(id).remove();
					$('.modal-backdrop.fade.in').remove();
				});

				defered.reject(response.metaData.errors);
			} else {
				defered.resolve({
					data : response
				});
			}
		}).error(function(err) {
			$("html").append("Error");
			$(id).modal("show");
			$(id).on("hidden.bs.modal", function() {
				$(id).remove();
				$state.go('Index');
			});

			defered.reject(err);
		});

		return promise;
	}

	function cerrarMensaje() {
		FactoryLoader.desactivar();
	}

	function callAPI(path, _data, namespacePath, message) {
		
		if (message) {
			FactoryLoader.activar(message);
		}
		
		if (_data.metaData) {
			_data.metaData.namespace = "cl.sii.sdi.lob.juridica.acj.data.impl." + namespacePath;
		}
		var url = host + CONFIG.app + path;
		return controlDeErrores($http.post(url, _data, config));
	}
	
	function callAPINoData(path, namespacePath, message) {
		
		var metadata = {
			"namespace" : "cl.sii.sdi.lob.juridica.acj.data.impl." + namespacePath,
			"conversationId" : token,
			"transactionId" : createUUID(),
			"page" : null
		};
		
		if (message) {
			FactoryLoader.activar(message);
		}
		
		var url = host + CONFIG.app + path;
		return controlDeErrores($http.post(url, metadata, config));
	}
	
	/*------------------------------------------------ Pronunciamientos ---------------------------------------*/

	function getFullPronunciamiento(id, message) {
		data.data = {
			id : id
		};
		var path = '/services/data/internetService/pronunciamientos/get-full';
		var namespacePath = "InternetApplicationService/getFullPronunciamiento";

		return callAPI(path, data, namespacePath, message);
	}
	
	function FilterTipoCodigo(term) {
		var data = {
			metaData : {
				"namespace" : null,
				"conversationId" : token,
				"transactionId" : createUUID(),
				"page" : null
			},
			data : {}
		};
		data.data = {
			orderByField: "fecha",
			orderByOrder: "desc",
			conditions: [{
				field : "codigoPronunciamiento",
				operator : "like",
				value : "%"+ term + "%",
				caseInsensitive : true
			}]
		};
		var namespacePath = "InternetApplicationService/filterPronunciamientos";
		var path = "/services/data/internetService/pronunciamientos/filter";
		if (data.metaData) {
			data.metaData.namespace = "cl.sii.sdi.lob.juridica.acj.data.impl." + namespacePath;
		}
		var url = host + CONFIG.app + path;
		return controlDeErrores($http.post(url, data, config));
	}

	/*---------------------------------------------------------------------------------------------------------*/
	/*----------------------------------------------- Resumenes -----------------------------------------------*/

	function getTipoInstancia(id, message) {
		data.data = {
			id : id
		};
		var namespacePath = "InternetApplicationService/getTipoInstancia";
		var path = "/services/data/internetService/tipos-instancia/get";
		return callAPI(path, data, namespacePath, message);
	}
	
	function getGrupoInstancia(id, message) {
		data.data = {
			id : id
		};
		var namespacePath = "InternetApplicationService/getGrupoInstancia";
		var path = "/services/data/internetService/grupos-instancias/get";
		return callAPI(path, data, namespacePath, message);
	}
	
	function getCuerpoNormativo(id, message) {
		data.data = {
			id : id
		};
		var namespacePath = "InternetApplicationService/getCuerpoNormativo";
		var path = "/services/data/internetService/cuerpos-normativos/get";
		return callAPI(path, data, namespacePath, message);
	}
	
	function getArticulo(id, message) {
		data.data = {
			id : id
		};
		var namespacePath = "InternetApplicationService/getArticulo";
		var path = "/services/data/internetService/articulos/get";
		return callAPI(path, data, namespacePath, message);
	}
	
	function getResumenNivel1(idTipoInstancia, message) {
		data.data = {
			id : idTipoInstancia
		};
		var namespacePath = "InternetApplicationService/getPronunciamientosPorCuerpoNormativoYGrupoInstancia";
		var path = "/services/data/internetService/pronunciamientos-por-cuerpo-normativo-y-grupo-instancia";
		return callAPI(path, data, namespacePath, message);
	}

	function getResumenNivel2(grupoInstancia, cuerpoNormativo, message) {
		data.data = {
			grupoInstancia: grupoInstancia,
			cuerpoNormativo: cuerpoNormativo
		};
		var namespacePath = "InternetApplicationService/getPronunciamientosPorArticuloYGrupoInstancia";
		var path = "/services/data/internetService/pronunciamientos-por-articulo-y-grupo-instancia";
		return callAPI(path, data, namespacePath, message);
	}

	function getResultadosExplorador(articulo, grupoInstancia, message) {
		data.data = {
			articulo : articulo,
			grupoInstancia : grupoInstancia
		};
		var namespacePath = "InternetApplicationService/getResultadosExplorador";
		var path = "/services/data/internetService/resultados-explorador";
		return callAPI(path, data, namespacePath, message);
	}

	/*---------------------------------------------------------------------------------------------------------*/
	/*------------------------------------------------ Buscador -----------------------------------------------*/
	
	function listTiposInstancia(message) {
		var namespacePath = "InternetApplicationService/listTiposInstancia";
		var path = "/services/data/internetService/tipos-instancia";
		return callAPINoData(path, namespacePath, message);
	}

	function findGruposDeInstancia(idTipoInstancia, message) {
		data.data = {
			id : idTipoInstancia
		};
		var namespacePath = "InternetApplicationService/findGruposDeInstancia";
		var path = "/services/data/internetService/find-grupos-instancia";
		return callAPI(path, data, namespacePath, message);
	}
	
	function findTiposDePronunciamiento(idTipoInstancia, message) {
		data.data = {
			id : idTipoInstancia
		};
		var namespacePath = "InternetApplicationService/findTiposDePronunciamiento";
		var path = "/services/data/internetService/find-tipos-pronunciamiento";
		return callAPI(path, data, namespacePath, message);
	}

	function findInstancias(idGrupoInstancia, message) {
		data.data = {
			id : idGrupoInstancia
		};
		var namespacePath = "InternetApplicationService/findInstancias";
		var path = "/services/data/internetService/find-instancias";
		return callAPI(path, data, namespacePath, message);
	}

	function listCuerposNormativos(message) {
		var namespacePath = "InternetApplicationService/listCuerposNormativos";
		var path = "/services/data/internetService/cuerpos-normativos";
		return callAPINoData(path, namespacePath, message);
	}

	function findArticulos(idCuerpoNormativo, message) {
		data.data = {
			id : idCuerpoNormativo
		};
		var namespacePath = "InternetApplicationService/findArticulos";
		var path = "/services/data/internetService/find-articulos";
		return callAPI(path, data, namespacePath, message);
	}
	
	function findPronunciamientos(searchForm, message) {
		data.data = searchForm;
		var namespacePath = "InternetApplicationService/findPronunciamientos";
		var path = "/services/data/internetService/find-pronunciamientos";
		return callAPI(path, data, namespacePath, message);
	}
	
	function findTipoCodigo() {
		var data = {
				metaData : {
					"namespace" : null,
					"conversationId" : token,
					"transactionId" : createUUID(),
					"page" : null
				},
				data : {}
			};
		var tipoCodigoConditions = [ {
			field : "idTipoInstancia",
			operator : "=",
			value : 1
		} ];
		data.data.conditions = tipoCodigoConditions;
		var namespacePath = "InternetApplicationService/findTipoCodigo";
		var path = "/services/data/internetService/tipos-codigo/filter";
		return callAPI(path, data, namespacePath, "Cargando...");
	}
	
	/*---------------------------------------------------------------------------------------------------------*/
	
	// Servicios expuestos por la API
	return {
		getHost : getHost,
		cerrarMensaje : cerrarMensaje,
		FilterTipoCodigo : FilterTipoCodigo,
		getTipoInstancia : getTipoInstancia,
		getGrupoInstancia : getGrupoInstancia,
		getCuerpoNormativo : getCuerpoNormativo,
		getArticulo : getArticulo,
		getResumenNivel1 : getResumenNivel1,
		getResumenNivel2 : getResumenNivel2,
		getResultadosExplorador : getResultadosExplorador,
		getFullPronunciamiento : getFullPronunciamiento,
		listTiposInstancia : listTiposInstancia,
		findGruposDeInstancia : findGruposDeInstancia,
		findTiposDePronunciamiento : findTiposDePronunciamiento,
		findInstancias : findInstancias,
		listCuerposNormativos : listCuerposNormativos,
		findArticulos : findArticulos,
		findTipoCodigo : findTipoCodigo,
		findPronunciamientos : findPronunciamientos
	};

}

angular.module('comun').service('ServiceHTTP', ServiceHTTP);
