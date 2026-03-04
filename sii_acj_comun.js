angular.module('comun', [ 'ui.bootstrap', 'ui.router', 'ngRoute', 'ngAnimate', 'cl.sii.settings', 'aaModule' ]);
angular.module('comun').config(function($stateProvider) {

	$stateProvider.state('Home', {
		url : '/:tipo_home',
		templateUrl : 'comun/partial/home/home.html',
		controller : 'Home',
		controllerAs : 'vm'
	});

	$stateProvider.state('Instancia', {
		url : '/instancia/:tipo_instancia',
		templateUrl : 'comun/partial/Instancia/Instancia.html',
		controller : 'Instancia',
		controllerAs : 'vm'
	});
	
	$stateProvider.state('CuerpoNormativo', {
		url : '/instancia/:tipo_instancia/:cuerpo_normativo/:grupo_instancia',
		templateUrl : 'comun/partial/CuerpoNormativo/CuerpoNormativo.html',
		controller : 'CuerpoNormativo',
		controllerAs : 'vm'
	});
	
	$stateProvider.state('Articulo', {
		url : '/filtro/:tipo_instancia/:cuerpo_normativo/:grupo_instancia/:articulo',
		templateUrl : 'comun/partial/Articulo/Articulo.html',
		controller : 'Articulo',
		controllerAs : 'vm'
	});

	$stateProvider.state('Pronunciamiento', {
		url : '/pronunciamiento/:id',
		templateUrl : 'comun/partial/Pronunciamiento/Pronunciamiento.html',
		controller : 'Pronunciamiento',
		controllerAs : 'vm'
	})
	
	$stateProvider.state('Buscador', {
		url : '/buscador/basico',
		templateUrl : 'comun/partial/Buscador/Buscador.html',
		controller : 'Buscador',
		controllerAs : 'vm'
	})
	
	$stateProvider.state('Resultados', {
		url : '/resultados/:tipo',
		templateUrl : 'comun/partial/Buscador/Buscador.html',
		controller : 'Buscador',
		controllerAs : 'vm'
	});
	
});

var isLocal = window.location.hostname == 'dev.sii.cl';
angular.module('comun').constant('CONFIG', {
    'app': 'acjui',
    'port': isLocal ? '8080' : window.location.port
}).constant('NEW_THRESHOLD', 30);


// posterior al app.config() , luego app.run(), directivas, app.controller()
angular
		.module('comun')
		.constant('PRONUNCIAMIENTO','pronunciamiento')				
		.run(
				function($rootScope, SIISettings) {
					SIISettings
							.load(
									'sdi.lob.juridica.acj',
									'cl.sii.sdi.lob.juridica.acj.data.impl.SettingsApplicationService/consultarParametros',
									'/acjui/services/data/settingsService/consultarParametros');
				});